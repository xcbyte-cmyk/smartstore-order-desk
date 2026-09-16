import io
import threading
from datetime import datetime, timedelta

import httpx
import pytest
from openpyxl import load_workbook
from starlette.testclient import TestClient

from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.orders_core import (
    KST,
    NaverOrders,
    OrderDB,
    OrderService,
    account_key,
    normalize,
    now,
    parse_invoices,
    workbook_bytes,
)
from korea_ecommerce_mcp.orders_schedule import (
    SCHEDULE_VERSION,
    is_collection_slot,
    next_collection,
)
from korea_ecommerce_mcp.orders_web import create_app


def settings():
    return Settings(
        _env_file=None, naver_client_id="test-account", naver_client_secret="test-secret"
    )


def order(oid="2026091600000001"):
    return normalize(
        {
            "order": {"orderId": "202609160001", "paymentDate": now().isoformat()},
            "productOrder": {
                "productOrderId": oid,
                "productName": "=1+1",
                "productOption": "대형",
                "quantity": 2,
                "productOrderStatus": "PAYED",
                "placeOrderStatus": "OK",
                "expectedDeliveryMethod": "DELIVERY",
                "shippingAddress": {
                    "name": "테스트 고객",
                    "zipCode": "01234",
                    "tel1": "01012345678",
                },
            },
        }
    )


class FakeAPI:
    def __init__(self, config):
        self.dispatched = []
        self.timeout = False
        self.partial = False
        self.fail_collect = False

    async def close(self):
        pass

    async def collect_window(self, begin, end):
        if self.fail_collect:
            raise ValueError("수집 실패 테스트")
        return [order()]

    async def details(self, ids):
        result = []
        for oid in ids:
            row = order(oid)
            sent = next((r for r in self.dispatched if r["id"] == oid), None)
            if sent and not self.timeout and not (self.partial and oid.endswith("2")):
                row.update(status="DELIVERING", tracking=sent["tracking"], carrier=sent["carrier"])
            result.append(row)
        return result

    async def dispatch(self, rows):
        self.dispatched.extend(rows)
        if self.timeout:
            raise httpx.ReadTimeout("ambiguous")
        return {
            "successProductOrderIds": [
                r["id"] for r in rows if not (self.partial and r["id"].endswith("2"))
            ],
            "failProductOrderInfos": [
                {"productOrderId": r["id"], "message": "취소 요청"}
                for r in rows
                if self.partial and r["id"].endswith("2")
            ],
        }


@pytest.fixture
def db(tmp_path):
    return OrderDB(tmp_path / "orders.sqlite3")


def invoice(oid="2026091600000001", tracking="001234567890"):
    return {"id": oid, "tracking": tracking, "carrier": "CJGLS", "error": "", "row": 2}


def test_excel_roundtrip_and_duplicate_rejection(db):
    db.upsert([order()])
    content = workbook_bytes(
        ["상품주문번호", "택배사", "송장번호"], [["2026091600000001", "CJ대한통운", "001234567890"]]
    )
    rows = parse_invoices(content, db.orders(), db)
    assert rows[0]["error"] == ""
    assert rows[0]["tracking"] == "001234567890"
    duplicate = workbook_bytes(
        ["상품주문번호", "택배사", "송장번호"],
        [["2026091600000001", "CJ대한통운", "001234567890"]] * 2,
    )
    assert all("중복" in r["error"] for r in parse_invoices(duplicate, db.orders(), db))


def test_excel_rejects_numeric_ids_and_unknown_orders(db):
    db.upsert([order()])
    content = workbook_bytes(
        ["상품주문번호", "택배사", "송장번호"],
        [
            [2026091600000001, "CJ대한통운", "001234567890"],
            ["2026091600000002", "CJ대한통운", "001234567890"],
        ],
    )
    rows = parse_invoices(content, db.orders(), db)
    assert "텍스트" in rows[0]["error"]
    assert "수집된 주문" in rows[1]["error"]


def test_export_prevents_formula_and_preserves_identifiers():
    content = workbook_bytes(
        ["상품명", "상품주문번호", "우편번호"], [["=1+1", "2026091600000001", "01234"]]
    )
    book = load_workbook(io.BytesIO(content), data_only=False)
    assert book.active["A2"].data_type == "s"
    assert book.active["B2"].value == "2026091600000001"
    assert book.active["C2"].value == "01234"
    book.close()


@pytest.mark.asyncio
async def test_collection_is_idempotent_and_preserves_failed_cursor(db):
    fake = FakeAPI(settings())
    service = OrderService(db, settings, lambda _: fake)
    await service.collect()
    await service.collect()
    assert len(db.orders()) == 1
    cursor = db.get("cursor")
    fake.fail_collect = True
    with pytest.raises(ValueError):
        await service.collect()
    assert db.get("cursor") == cursor
    assert db.get("last_error")


@pytest.mark.asyncio
async def test_pagination_and_korea_timezone():
    api = NaverOrders(settings())
    calls = []

    async def request(method, suffix, body=None, params=None):
        calls.append((suffix, dict(params or {})))
        if suffix == "/query":
            return [{"productOrder": {"productOrderId": oid}} for oid in body["productOrderIds"]]
        if "moreSequence" not in params:
            return {
                "lastChangeStatuses": [{"productOrderId": "2026091600000001"}],
                "more": {"moreFrom": now().isoformat(), "moreSequence": 42},
            }
        return {"lastChangeStatuses": [{"productOrderId": "2026091600000002"}]}

    api.request = request
    try:
        rows = await api.collect_window(now() - timedelta(hours=1), now())
        assert len(rows) == 2
        assert calls[0][1]["lastChangedFrom"].endswith("+09:00")
        assert calls[1][1]["moreSequence"] == 42
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_partial_dispatch_readback_and_token_consumption(db):
    db.upsert([order(), order("2026091600000002")])
    fake = FakeAPI(settings())
    fake.partial = True
    service = OrderService(db, settings, lambda _: fake)
    db.set("dispatch_enabled", True)
    token = db.preview([invoice(), invoice("2026091600000002")], account_key(settings()))
    rows = await service.send(token)
    assert [r["state"] for r in rows] == ["success", "failed"]
    with pytest.raises(ValueError):
        await service.send(token)
    assert len(fake.dispatched) == 2


@pytest.mark.asyncio
async def test_ambiguous_dispatch_never_retries(db):
    db.upsert([order()])
    fake = FakeAPI(settings())
    fake.timeout = True
    service = OrderService(db, settings, lambda _: fake)
    db.set("dispatch_enabled", True)
    await service.send(db.preview([invoice()], account_key(settings())))
    assert db.shipment(invoice()["id"])["state"] == "unknown"
    await service.send(db.preview([invoice()], account_key(settings())))
    assert len(fake.dispatched) == 1


@pytest.mark.asyncio
async def test_dispatch_gate_and_account_binding(db):
    service = OrderService(db, settings, FakeAPI)
    with pytest.raises(ValueError, match="활성화"):
        await service.send("missing")
    db.set("account", "other-account")
    with pytest.raises(ValueError, match="계정"):
        await service.collect()


def test_http_flow_export_upload_and_csrf(tmp_path):
    fake = FakeAPI(settings())
    app = create_app(tmp_path / "web.sqlite3", settings, lambda _: fake, scheduler=False)
    with TestClient(app) as client:
        data = client.get("/api/state").json()
        assert "test-secret" not in str(data)
        headers = {"x-csrf-token": data["csrf"]}
        assert client.post("/api/collect", json={}).status_code == 403
        assert (
            client.get("/api/state", headers={"Origin": "https://example.com"}).status_code == 403
        )
        assert client.post("/api/collect", json={}, headers=headers).status_code == 200
        oid = order()["id"]
        export = client.post("/api/export", json={"ids": [oid]}, headers=headers)
        assert export.status_code == 200 and export.content.startswith(b"PK")
        content = workbook_bytes(
            ["상품주문번호", "택배사", "송장번호"], [[oid, "CJGLS", "001234567890"]]
        )
        upload = client.post("/api/invoices/preview", content=content, headers=headers).json()
        assert upload["valid"] == 1
        app.state.db.set("dispatch_enabled", True)
        result = client.post(
            "/api/invoices/send",
            json={"token": upload["token"], "confirm": "EXECUTE"},
            headers=headers,
        )
        assert result.status_code == 200
        assert result.json()["results"][0]["state"] == "success"
        assert client.get("/").status_code == 200


def test_schedule_settings_survive_restart(tmp_path):
    path = tmp_path / "schedule.sqlite3"
    with TestClient(create_app(path, settings, FakeAPI, scheduler=False)) as client:
        headers = {"x-csrf-token": client.get("/api/state").json()["csrf"]}
        result = client.post("/api/schedule", json={"enabled": True}, headers=headers)
        assert result.status_code == 200
        expected = client.get("/api/state").json()["next_run"]
    with TestClient(create_app(path, settings, FakeAPI, scheduler=False)) as client:
        data = client.get("/api/state").json()
        assert data["schedule_label"] == "토~목 오후 6시 · 금요일 제외"
        assert data["next_run"] == expected


def test_due_schedule_collects_without_browser_request(tmp_path, monkeypatch):
    path = tmp_path / "automatic.sqlite3"
    db = OrderDB(path)
    fixed = datetime(2026, 9, 17, 18, 0, 5, tzinfo=KST)
    monkeypatch.setattr("korea_ecommerce_mcp.orders_web.now", lambda: fixed)
    db.set("schedule_version", SCHEDULE_VERSION)
    db.set("next_run", fixed.replace(second=0).isoformat())
    completed = threading.Event()

    class ScheduledAPI(FakeAPI):
        async def close(self):
            completed.set()

    with TestClient(create_app(path, settings, ScheduledAPI, scheduler=True)):
        assert completed.wait(3)
        assert len(db.orders()) == 1
        assert db.get("last_success")
        assert db.get("next_run") == "2026-09-19T18:00:00+09:00"


def test_blank_or_oversized_excel_is_rejected(db):
    with pytest.raises(ValueError):
        parse_invoices(workbook_bytes([], []), [], db)
    with pytest.raises(ValueError, match="정상적인"):
        parse_invoices(b"not a workbook", [], db)


@pytest.mark.parametrize("day", [14, 15, 16, 17, 19, 20])
def test_collection_runs_on_saturday_through_thursday(day):
    before = datetime(2026, 9, day, 17, 59, tzinfo=KST)
    due = next_collection(before)
    assert due.day == day and due.hour == 18
    assert is_collection_slot(due + timedelta(seconds=5), due)
    assert not is_collection_slot(due + timedelta(minutes=1), due)


def test_friday_and_missed_slots_are_excluded():
    thursday = datetime(2026, 9, 17, 18, tzinfo=KST)
    friday = datetime(2026, 9, 18, 18, tzinfo=KST)
    assert next_collection(thursday) == datetime(2026, 9, 19, 18, tzinfo=KST)
    assert next_collection(friday) == datetime(2026, 9, 19, 18, tzinfo=KST)
    assert not is_collection_slot(friday, friday)
    assert not is_collection_slot(friday, thursday)


@pytest.mark.asyncio
async def test_manual_collection_keeps_fixed_schedule(db):
    due = "2026-09-19T18:00:00+09:00"
    db.set("next_run", due)
    service = OrderService(db, settings, FakeAPI)
    await service.collect()
    assert db.get("next_run") == due


def test_existing_hourly_schedule_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite3"
    db = OrderDB(path)
    db.set("interval", 60)
    db.set("next_run", "2026-09-18T19:00:00+09:00")
    monkeypatch.setattr(
        "korea_ecommerce_mcp.orders_web.now", lambda: datetime(2026, 9, 18, 12, tzinfo=KST)
    )
    app = create_app(path, settings, FakeAPI, scheduler=False)
    assert app.state.db.get("next_run") == "2026-09-19T18:00:00+09:00"
    assert app.state.db.get("schedule_version") == SCHEDULE_VERSION
