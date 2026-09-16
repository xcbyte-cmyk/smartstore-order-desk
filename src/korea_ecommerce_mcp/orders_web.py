"""Loopback-only Korean order workspace. Run one worker per database."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn
from dotenv import set_key
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from korea_ecommerce_mcp.config import Settings
from korea_ecommerce_mcp.orders_core import (
    CARRIERS,
    NaverOrders,
    OrderDB,
    OrderService,
    account_key,
    now,
    parse_invoices,
    public_error,
    workbook_bytes,
)
from korea_ecommerce_mcp.orders_schedule import (
    SCHEDULE_LABEL,
    SCHEDULE_VERSION,
    is_collection_slot,
    next_collection,
)

STATIC = Path(__file__).with_name("orders_static")


def create_app(db_path=None, settings_factory=Settings, api_factory=NaverOrders, scheduler=True):
    db = OrderDB(db_path or os.environ.get("KEIC_ORDERS_DATABASE_PATH", "orders_workspace.sqlite3"))
    service = OrderService(db, settings_factory, api_factory)
    csrf = secrets.token_urlsafe(32)
    if db.get("schedule_version") != SCHEDULE_VERSION:
        db.set("schedule_version", SCHEDULE_VERSION)
        db.set("next_run", next_collection(now()).isoformat())
        db.set("auto_enabled", True)
    elif db.get("next_run") is None:
        db.set("next_run", next_collection(now()).isoformat())

    async def scheduled_loop():
        while True:
            current = now()
            due = datetime.fromisoformat(db.get("next_run"))
            if current >= due:
                in_slot = is_collection_slot(current, due)
                if not in_slot or not service.lock.locked():
                    # Claim before collecting: failures/restarts cannot repeat this slot.
                    db.set("next_run", next_collection(current).isoformat())
                    settings = settings_factory()
                    if (
                        in_slot
                        and db.get("auto_enabled", True)
                        and settings.naver_client_id
                        and settings.naver_client_secret
                    ):
                        with contextlib.suppress(ValueError):
                            await service.collect()
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(scheduled_loop()) if scheduler else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def error_handler(request, exc):
        return JSONResponse({"error": public_error(exc)}, status_code=400)

    async def index(request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def state(request):
        settings = settings_factory()
        orders = db.orders()
        return JSONResponse(
            {
                "csrf": csrf,
                "orders": orders,
                "events": db.events(),
                "configured": bool(settings.naver_client_id and settings.naver_client_secret),
                "client_id_hint": (settings.naver_client_id or "")[-4:],
                "account_id": settings.naver_account_id or "",
                "busy": service.busy,
                "auto_enabled": db.get("auto_enabled", True),
                "schedule_label": SCHEDULE_LABEL,
                "dispatch_enabled": db.get("dispatch_enabled", False),
                "last_success": db.get("last_success"),
                "last_error": db.get("last_error", ""),
                "next_run": db.get("next_run"),
                "carriers": CARRIERS,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def collect(request):
        data = await request.json()
        days = data.get("days")
        if days not in [None, 1, 3, 7, 30]:
            raise ValueError("수집 기간은 1·3·7·30일 중 선택해 주세요.")
        count = await service.collect(days)
        return JSONResponse({"updated": count})

    async def schedule(request):
        data = await request.json()
        enabled = data.get("enabled")
        if type(enabled) is not bool:
            raise ValueError("자동 수집 설정을 확인해 주세요.")
        db.set("auto_enabled", enabled)
        # Preserve the claimed slot when a user toggles during 18:00.
        if datetime.fromisoformat(db.get("next_run")) < now():
            db.set("next_run", next_collection(now()).isoformat())
        db.event("info", f"자동 수집 {'켜짐' if enabled else '꺼짐'} · {SCHEDULE_LABEL}")
        return JSONResponse({"ok": True})

    async def connection(request):
        if service.lock.locked():
            raise ValueError("작업 완료 후 연결 설정을 바꿔 주세요.")
        data = await request.json()
        settings = settings_factory()
        client = str(data.get("client_id", "")).strip() or settings.naver_client_id
        secret = str(data.get("client_secret", "")).strip() or settings.naver_client_secret
        account = str(data.get("account_id", "")).strip()
        enabled = data.get("dispatch_enabled", False)
        if not client or not secret:
            raise ValueError("Client ID와 Client Secret을 입력해 주세요.")
        if any("\n" in x or "\r" in x for x in [client, secret, account]):
            raise ValueError("연결 정보에는 줄바꿈을 넣을 수 없습니다.")
        if type(enabled) is not bool:
            raise ValueError("송신 설정이 올바르지 않습니다.")
        candidate = settings.model_copy(
            update={
                "naver_client_id": client,
                "naver_client_secret": secret,
                "naver_account_id": account or None,
            }
        )
        if db.get("account") and db.get("account") != account_key(candidate):
            raise ValueError("기존 주문이 있는 계정은 다른 계정으로 변경할 수 없습니다.")
        for key, value in [
            ("KEIC_NAVER_CLIENT_ID", client),
            ("KEIC_NAVER_CLIENT_SECRET", secret),
            ("KEIC_NAVER_ACCOUNT_ID", account),
        ]:
            set_key(".env", key, value)
            # Explicit process values must not shadow a newly saved local setting.
            os.environ[key] = value
        db.set("dispatch_enabled", enabled)
        db.event("info", "스마트스토어 연결 정보 저장 · 인증은 연결 확인으로 점검")
        return JSONResponse({"ok": True})

    async def connection_test(request):
        settings = service.settings()
        api = api_factory(settings)
        try:
            await api.adapter._token()
        finally:
            await api.close()
        db.event("success", "스마트스토어 API 인증 성공 · 주문 수집 권한은 수집 시 확인")
        return JSONResponse({"ok": True})

    async def export(request):
        data = await request.json()
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("내려받을 주문을 선택해 주세요.")
        selected = [o for o in db.orders() if o["id"] in set(ids)]
        if len(selected) != len(set(ids)):
            raise ValueError("일부 주문을 찾을 수 없습니다. 목록을 새로고침해 주세요.")
        headers = [
            "상품주문번호",
            "주문번호",
            "결제일시",
            "상품명",
            "옵션",
            "수량",
            "수취인",
            "연락처",
            "우편번호",
            "주소",
            "배송메모",
            "주문상태",
            "발주상태",
        ]
        keys = [
            "id",
            "order_id",
            "date",
            "name",
            "option",
            "quantity",
            "recipient",
            "phone",
            "postcode",
            "address",
            "memo",
            "status",
            "place_status",
        ]
        content = workbook_bytes(headers, [[o[k] for k in keys] for o in selected])
        db.event("info", f"발주서 다운로드 · 선택 {len(selected)}건")
        return excel(content, "orders.xlsx")

    def excel(content, filename):
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    async def template(request):
        return excel(
            workbook_bytes(["상품주문번호", "택배사", "송장번호"], [], "송장업로드", True),
            "invoice-template.xlsx",
        )

    async def upload(request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 5 * 1024 * 1024:
                raise ValueError("파일은 5MB 이하로 올려 주세요.")
        rows = parse_invoices(bytes(content), db.orders(), db)
        token = db.preview(rows, account_key(settings_factory()))
        return JSONResponse(
            {"rows": rows, "token": token, "valid": sum(not r["error"] for r in rows)}
        )

    async def send(request):
        data = await request.json()
        if data.get("confirm") != "EXECUTE":
            raise ValueError("송신 전 내용을 확인해 주세요.")
        result = await service.send(data.get("token", ""))
        return JSONResponse({"results": result})

    routes = [
        Route("/", index),
        Route("/api/state", state),
        Route("/api/collect", collect, methods=["POST"]),
        Route("/api/schedule", schedule, methods=["POST"]),
        Route("/api/connection", connection, methods=["POST"]),
        Route("/api/connection/test", connection_test, methods=["POST"]),
        Route("/api/export", export, methods=["POST"]),
        Route("/api/invoices/template", template),
        Route("/api/invoices/preview", upload, methods=["POST"]),
        Route("/api/invoices/send", send, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC)),
    ]
    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={ValueError: error_handler, httpx.HTTPError: error_handler},
    )
    app.state.db, app.state.service = db, service

    class LocalGuard(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"error": "이 화면에서만 요청할 수 있습니다."}, status_code=403)
            if request.method not in ["GET", "HEAD"] and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), csrf
            ):
                return JSONResponse({"error": "화면을 새로고침해 주세요."}, status_code=403)
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
            )
            return response

    app.add_middleware(LocalGuard)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    return app


def main():
    # The lock also prevents a second scheduler on a different port.
    import msvcrt

    lock = open("orders-ui.lock", "a+b")
    lock.seek(0)
    if not lock.read(1):
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("주문 관리 서버가 이미 실행 중입니다.") from None
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=8435, access_log=False)
    finally:
        lock.close()


if __name__ == "__main__":
    main()
