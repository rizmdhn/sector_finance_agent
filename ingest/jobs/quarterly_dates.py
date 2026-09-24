"""Daily job: diff the quarterly-dates universe feed against the stored snapshot.

BLOCKED (2026-09-24): no working path was found for a bulk quarterly-dates feed
despite direct probing against the live API — neither a dedicated endpoint nor a
screener field (`sector_agents/data/sectors_client.py`'s module docstring has the
full discovery notes; every guessed field name came back 400 INVALID_WHERE_CLAUSE
with "Invalid field name", not a formatting issue). This job cannot run as designed.

This is the change detector: on diff, bump ver:idx:{symbol} for each changed symbol
and fund_epoch once. See idx_agent_infrastructure_diagrams_md.md section 7 and
sectors_idx_ingest_cache_plan_md.md section 2 (Latest Quarterly Financial Dates).

Open design question for whoever picks this up: redesign around
client.get_quarterly_financials(symbol) polled per symbol (962 symbols — check the
credit cost of a daily/weekly full sweep before committing to it) instead of a single
bulk diff, or find the real bulk endpoint by checking Sectors' actual dashboard/docs
rather than further guessing paths.
"""

from data.cache import Cache
from data.db import Database
from data.sectors_client import SectorsClient


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    raise NotImplementedError(
        "no working bulk quarterly-dates endpoint was found (see module docstring); "
        "this job needs a redesign before it can run"
    )
