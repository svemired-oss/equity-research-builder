"""Reserve a worst-case OpenAI charge before every hosted request."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

ACTIVE = ContextVar("openai_spend_budget", default=None)
MODEL = "gpt-5-mini-2025-08-07"


def daily_limit(value):
    try:
        units = Decimal(value)*1_000_000
        if units.is_finite() and units == units.to_integral_value() and 0 < units <= 10_000_000:
            return int(units)
    except (TypeError, InvalidOperation):
        pass
    raise ValueError("Set OPENAI_DAILY_CAP_USD to a positive decimal amount of at most $10.")


def worst_case(payload):
    # Full context charged at uncached published list price. This also covers
    # implicit framing and reasoning tokens within the supplied output cap.
    if (payload.get("model") != MODEL or payload.get("tools") or payload.get("service_tier") not in (None,"default")
            or type(payload.get("max_output_tokens")) is not int or not 1 <= payload["max_output_tokens"] <= 4600):
        raise ValueError("Hosted OpenAI requests must use the priced snapshot and bounded text-only settings.")
    return 100_000+2*payload["max_output_tokens"]


def reserve(payload):
    context = ACTIVE.get()
    if context is None:
        return
    jobs, lock, limit = context
    with lock:
        allowed = jobs.reserve_spend(datetime.now(timezone.utc).date().isoformat(),worst_case(payload),limit)
    if not allowed:
        from .operating_research import ResearchGap
        raise ResearchGap("The daily OpenAI spend cap has been reached. Cached downloads remain available. "
                          "New spending is available after midnight UTC.")


@contextmanager
def enforce(context):
    token = ACTIVE.set(context)
    try:
        yield
    finally:
        ACTIVE.reset(token)

