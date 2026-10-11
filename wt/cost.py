"""Jev spend. Input tokens are billed. Output tokens are free.

The desk can override the price and the monthly cap in the store meta.
Those overrides win over the environment on the next call.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone

DEFAULT_USD_PER_MTOK = 0.042
DEFAULT_MONTHLY_USD = 10.0
DEFAULT_DAILY_WARN_USD = 1.0


def input_cost(tokens: int, usd_per_mtok: float) -> float:
    """Dollars for this many input tokens. Output is not billed."""
    if tokens <= 0 or usd_per_mtok <= 0:
        return 0.0
    return (int(tokens) / 1_000_000) * float(usd_per_mtok)


def estimate_tokens(*parts: str) -> int:
    """Rough token count when the API omits usage.input_tokens."""
    text = "".join(parts)
    return max(1, len(text) // 4)


def project_month(month_usd: float, now: datetime) -> float:
    """Straight-line projection: spend so far times the rest of the UTC month."""
    if month_usd <= 0:
        return 0.0
    when = _aware(now)
    days = calendar.monthrange(when.year, when.month)[1]
    return month_usd / when.day * days


def resolve_rate(store, fallback: float) -> float:
    return _meta_float(store, "jev_usd_per_mtok", fallback)


def resolve_monthly(store, fallback: float) -> float:
    return _meta_float(store, "jev_monthly_usd", fallback)


def resolve_daily_warn(store, fallback: float) -> float:
    return _meta_float(store, "jev_daily_warn_usd", fallback)


def record_usage(
    store,
    *,
    run_id: str,
    input_tokens: int,
    output_tokens: int,
    usd: float,
    cached: int,
    calls: int,
    created_at: datetime | None = None,
) -> None:
    if store is None:
        return
    when = _aware(created_at or _utcnow())
    store.conn.execute(
        """
        INSERT INTO jev_usage(
          created_at, run_id, input_tokens, output_tokens, usd, cached, calls
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            when.isoformat(),
            run_id,
            int(input_tokens),
            int(output_tokens),
            float(usd),
            int(cached),
            int(calls),
        ),
    )
    store.conn.commit()


def over_budget(month_usd: float, cap: float) -> bool:
    return cap > 0 and month_usd >= cap


def summary(store, *, rate: float, monthly: float, daily_warn: float, now: datetime | None = None) -> dict:
    when = _aware(now or _utcnow())
    today = _sum(store, _day_start(when), _next_day(when))
    month = _sum(store, _month_start(when), _next_month(when))
    last = _last_run(store)
    cached = _cached_count(store, _month_start(when), _next_month(when))
    month_usd = month["usd"]
    today_usd = today["usd"]
    return {
        "usd_per_mtok": rate,
        "monthly_usd": monthly,
        "daily_warn_usd": daily_warn,
        "today_usd": today_usd,
        "month_usd": month_usd,
        "projected_usd": project_month(month_usd, when),
        "today_tokens": today["tokens"],
        "month_tokens": month["tokens"],
        "last_run_tokens": last["tokens"],
        "last_run_usd": last["usd"],
        "last_run_id": last["run_id"],
        "cached_hits": cached,
        "over_budget": over_budget(month_usd, monthly),
        "daily_warning": daily_warn > 0 and today_usd >= daily_warn,
        "output_billed": False,
        "fallback": (
            "When the monthly cap is hit, the next items are scored by local Kev "
            "if it is up, otherwise the lexical decider."
        ),
        "note": (
            "This panel reads the desk database. wt run writes spend to WT_DB, "
            "which is a different file unless you point them at the same path."
        ),
    }


def _meta_float(store, key: str, fallback: float) -> float:
    if store is None:
        return float(fallback)
    raw = store.meta_get(key)
    if raw is None or not str(raw).strip():
        return float(fallback)
    try:
        value = float(raw)
    except ValueError:
        return float(fallback)
    if value < 0:
        return float(fallback)
    return value


def _sum(store, start: datetime, end: datetime) -> dict:
    if store is None:
        return {"usd": 0.0, "tokens": 0}
    row = store.conn.execute(
        """
        SELECT COALESCE(SUM(usd), 0) AS usd, COALESCE(SUM(input_tokens), 0) AS tokens
        FROM jev_usage
        WHERE created_at >= ? AND created_at < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return {"usd": float(row["usd"]), "tokens": int(row["tokens"])}


def _cached_count(store, start: datetime, end: datetime) -> int:
    if store is None:
        return 0
    row = store.conn.execute(
        """
        SELECT COALESCE(SUM(cached), 0) AS cached
        FROM jev_usage
        WHERE created_at >= ? AND created_at < ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    return int(row["cached"])


def _last_run(store) -> dict:
    empty = {"tokens": 0, "usd": 0.0, "run_id": ""}
    if store is None:
        return empty
    row = store.conn.execute(
        "SELECT run_id FROM jev_usage ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return empty
    run_id = str(row["run_id"])
    totals = store.conn.execute(
        """
        SELECT COALESCE(SUM(input_tokens), 0) AS tokens, COALESCE(SUM(usd), 0) AS usd
        FROM jev_usage WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    return {"tokens": int(totals["tokens"]), "usd": float(totals["usd"]), "run_id": run_id}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_start(when: datetime) -> datetime:
    when = _aware(when)
    return when.replace(hour=0, minute=0, second=0, microsecond=0)


def _next_day(when: datetime) -> datetime:
    from datetime import timedelta

    return _day_start(when) + timedelta(days=1)


def _month_start(when: datetime) -> datetime:
    when = _aware(when)
    return when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(when: datetime) -> datetime:
    start = _month_start(when)
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)
