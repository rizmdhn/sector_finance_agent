"""Daily job: diff the quarterly-dates universe feed against the stored snapshot.

This is the change detector: on diff, bump ver:idx:{symbol} for each changed symbol
and fund_epoch once. See idx_agent_infrastructure_diagrams_md.md section 7 and
sectors_idx_ingest_cache_plan_md.md section 2 (Latest Quarterly Financial Dates).
"""

from datetime import date

from data.cache import EPOCH_FUND, Cache
from data.db import Database
from data.sectors_client import SectorsClient


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    raw = client.get_quarterly_dates_universe()
    latest = {symbol: date.fromisoformat(report_date) for symbol, report_date in raw.items()}

    changed = db.diff_quarterly_dates(latest)
    if not changed:
        return

    for symbol in changed:
        cache.bump_symbol_version(symbol)
    cache.bump_epoch(EPOCH_FUND)
