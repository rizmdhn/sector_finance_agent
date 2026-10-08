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

import logging
from datetime import date, timedelta

from data.cache import EPOCH_PRICE, Cache
from data.canonical import idx_today
from data.db import Database
from data.sectors_client import SectorsAPIError, SectorsClient, SectorsRateLimitError

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

logger = logging.getLogger(__name__)



def _weekdays_between(start: date, end: date) -> list[date]:
    """Every Mon-Fri date in [start, end], inclusive."""
    return [start + timedelta(days=n) for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() < 5]


def _price_row(r: dict) -> dict:
    return {
        "symbol": r["symbol"],
        "trade_date": date.fromisoformat(r["date"]),
        "open": None,
        "high": None,
        "low": None,
        "close": r.get("close"),
        "volume": None,
        "market_cap": None,
    }


def _fetch_close(db: Database, client: SectorsClient, trade_date: date) -> int:
    """Load one trading day page by page, writing each page to Postgres as it arrives and
    recording how far it got (close_ingest_progress). A 429 therefore keeps every page
    already paid for, and the next run resumes at the page that failed. Returns rows
    written by this call (0 = holiday / not published yet / already complete)."""
    progress = db.get_close_progress(trade_date)
    if progress and progress["complete"]:
        return 0
    offset = progress["next_offset"] if progress else 0
    total = progress["total_count"] if progress else None
    written = 0
    while True:
        try:
            response = client.get_daily_universe_close(trade_date.isoformat(), offset=offset)
        except SectorsRateLimitError as exc:
            raise SectorsRateLimitError(
                429,
                f"rate limited on {trade_date} at offset {offset} of {total or '?'}; rows before it are "
                f"saved, run again to continue from there ({exc})",
            ) from exc
        except SectorsAPIError as exc:
            # Live (2026-10-01): for idx_today() Sectors can 400 with "Date cannot be in
            # the future" (its clock lags Jakarta's) — same as not published yet.
            if "in the future" in str(exc).lower():
                return written
            raise
        page = response.get("results", [])
        if not page and offset == 0:
            return 0  # holiday, or today's close not published yet — try again next poll
        db.upsert_price_rows([_price_row(r) for r in page])
        written += len(page)
        pagination = response.get("pagination", {})
        total = pagination.get("total_count", total)
        has_next = bool(pagination.get("has_next")) and bool(page)
        offset = pagination.get("next_offset", offset + len(page))
        db.save_close_progress(trade_date, offset, total, complete=not has_next)
        if not has_next:
            return written
        logger.info("close %s: %s/%s rows", trade_date, offset, total)


def run(
    db: Database, cache: Cache, client: SectorsClient, today: date | None = None, max_backfill_days: int = MAX_BACKFILL_DAYS
) -> None:
    today = today or idx_today()
    latest = db.latest_complete_close_date()
    pending = db.incomplete_close_dates()  # days a 429 (or a crash) cut off part-way
    if latest == today and not pending:
        return

    # An empty database gets the same capped backfill window as a long outage (live bug
    # 2026-10-01: starting at today alone could leave it empty for good). The cap keeps a
    # long outage from turning into hundreds of paid pages; pass a smaller
    # max_backfill_days for a lighter manual catch-up.
    start = (latest + timedelta(days=1)) if latest else today - timedelta(days=max_backfill_days)
    if start < today - timedelta(days=max_backfill_days):
        start = today - timedelta(days=max_backfill_days)

    bumped = False
    for trade_date in sorted(set(pending) | set(_weekdays_between(start, today))):
        if _fetch_close(db, client, trade_date):
            bumped = True

    if bumped:
        cache.bump_epoch(EPOCH_PRICE)
