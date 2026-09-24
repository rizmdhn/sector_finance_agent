"""Daily job: pull the full-universe close, poll until today's trading date appears.

The scheduler (ingest/scheduler.py) invokes this repeatedly during the post-close
window; `run` is a no-op once today's date is already stored, so re-polling is safe.
On a new trading date: bump price_epoch. See idx_agent_infrastructure_diagrams_md.md
section 7 and sectors_idx_ingest_cache_plan_md.md section 2 (Daily Full-Universe Close).

Confirmed live (2026-09-24) against close/: this bulk feed returns ONLY
symbol/date/close — no open/high/low/volume/market_cap — and is hard-capped at 30
rows/page (962 symbols -> ~33 pages), regardless of a requested `limit`. Those OHLCV/
market_cap columns are left NULL here; data/repositories.py::ensure_price_detail
backfills them per symbol on demand from the per-symbol daily/{symbol}/ endpoint,
which does have full OHLCV + market cap.
"""

from datetime import date

from data.cache import EPOCH_PRICE, Cache
from data.db import Database
from data.sectors_client import SectorsClient


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    today = date.today()
    if db.latest_trade_date() == today:
        return

    rows = []
    offset = 0
    while True:
        response = client.get_daily_universe_close(today.isoformat(), offset=offset)
        page = response.get("results", [])
        rows.extend(page)
        pagination = response.get("pagination", {})
        if not pagination.get("has_next"):
            break
        offset = pagination.get("next_offset", offset + len(page))

    if not rows:
        return  # today's data has not landed yet; the scheduler retries later

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
    cache.bump_epoch(EPOCH_PRICE)
