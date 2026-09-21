"""Daily job: pull the full-universe close, poll until today's trading date appears.

The scheduler (ingest/scheduler.py) invokes this repeatedly during the post-close
window; `run` is a no-op once today's date is already stored, so re-polling is safe.
On a new trading date: bump price_epoch. See idx_agent_infrastructure_diagrams_md.md
section 7 and sectors_idx_ingest_cache_plan_md.md section 2 (Daily Full-Universe Close).
"""

from datetime import date

from data.cache import EPOCH_PRICE, Cache
from data.db import Database
from data.sectors_client import SectorsClient


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    today = date.today()
    if db.latest_trade_date() == today:
        return

    rows = client.get_daily_universe_close(today.isoformat())
    if not rows:
        return  # today's data has not landed yet; the scheduler retries later

    db.upsert_price_rows(
        [
            {
                "symbol": r["symbol"],
                "trade_date": today,
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume": r.get("volume"),
                "market_cap": r.get("market_cap"),
            }
            for r in rows
        ]
    )
    cache.bump_epoch(EPOCH_PRICE)
