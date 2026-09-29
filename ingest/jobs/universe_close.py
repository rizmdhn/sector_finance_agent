"""Daily job: pull the full-universe close, poll until today's trading date appears.
Also catches up any trading dates between the last stored one and today, so a worker
outage (crash, redeploy, host down) across one or more trading days doesn't leave a
permanent gap — the old version only ever asked for `date.today()`, so a missed
poll window was gone for good (see PROGRESS.md's backfill gap finding).

The scheduler (ingest/scheduler.py) invokes this repeatedly during the post-close
window; `run` is a no-op once today's date is already stored, so re-polling is safe.
On any new trading date: bump price_epoch once, not per date. See
idx_agent_infrastructure_diagrams_md.md section 7 and
sectors_idx_ingest_cache_plan_md.md section 2 (Daily Full-Universe Close, which
explicitly warns "N days is N x pages of calls, estimate the credit cost").

Confirmed live (2026-09-24) against close/: this bulk feed returns ONLY
symbol/date/close — no open/high/low/volume/market_cap — and is hard-capped at 30
rows/page (962 symbols -> ~33 pages), regardless of a requested `limit`. Those OHLCV/
market_cap columns are left NULL here; data/repositories.py::ensure_price_detail
backfills them per symbol on demand from the per-symbol daily/{symbol}/ endpoint,
which does have full OHLCV + market cap.
"""

from datetime import date, timedelta

from data.cache import EPOCH_PRICE, Cache
from data.canonical import idx_today
from data.db import Database
from data.sectors_client import SectorsClient

# ponytail: no IDX trading-holiday calendar, so a weekday holiday in the backfill
# window gets re-fetched (1 empty page, cheap) on every run forever, same ambiguity
# the old code already had for "today hasn't landed yet". Add a calendar if that
# ever shows up as real wasted credit.
#
# Caps how many trading days back a single restart will try to backfill — the
# ingest plan doc's own warning (see module docstring) is "N days is N x ~33-page
# calls", so an outage of weeks should not silently trigger hundreds of calls on
# the next run. Raise this (or add an explicit one-off "backfill everything" script)
# if a real outage ever needs more than 2 weeks of catch-up.
MAX_BACKFILL_DAYS = 14


def _weekdays_between(start: date, end: date) -> list[date]:
    """Every Mon-Fri date in [start, end], inclusive."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() < 5]


def _fetch_close(client: SectorsClient, trade_date: date) -> list[dict]:
    rows = []
    offset = 0
    while True:
        response = client.get_daily_universe_close(trade_date.isoformat(), offset=offset)
        page = response.get("results", [])
        rows.extend(page)
        pagination = response.get("pagination", {})
        if not pagination.get("has_next"):
            break
        offset = pagination.get("next_offset", offset + len(page))
    return rows


def run(db: Database, cache: Cache, client: SectorsClient, today: date | None = None) -> None:
    today = today or idx_today()
    latest = db.latest_trade_date()
    if latest == today:
        return

    start = (latest + timedelta(days=1)) if latest else today
    if start < today - timedelta(days=MAX_BACKFILL_DAYS):
        start = today - timedelta(days=MAX_BACKFILL_DAYS)

    bumped = False
    for trade_date in _weekdays_between(start, today):
        rows = _fetch_close(client, trade_date)
        if not rows:
            continue  # holiday, or (only possible for trade_date == today) not landed yet
        db.upsert_price_rows(
            [
                {
                    "symbol": r["symbol"],
                    "trade_date": date.fromisoformat(r["date"]),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": r.get("close"),
                    "volume": None,
                    "market_cap": None,
                }
                for r in rows
            ]
        )
        bumped = True

    if bumped:
        cache.bump_epoch(EPOCH_PRICE)
