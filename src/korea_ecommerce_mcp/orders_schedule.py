"""One scheduled attempt at 18:00 KST, Saturday through Thursday."""

from datetime import timedelta

from korea_ecommerce_mcp.orders_core import KST

SCHEDULE_VERSION = "sat-thu-1800-v1"
SCHEDULE_LABEL = "토~목 오후 6시 · 금요일 제외"


def next_collection(after):
    current = after.astimezone(KST)
    candidate = current.replace(hour=18, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() == 4:
        candidate += timedelta(days=1)
    return candidate


def is_collection_slot(current, due):
    local = current.astimezone(KST)
    return (
        local.weekday() != 4
        and local.hour == 18
        and local.minute == 0
        and local.date() == due.astimezone(KST).date()
        and due <= current
    )
