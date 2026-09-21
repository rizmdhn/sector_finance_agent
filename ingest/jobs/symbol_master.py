"""Weekly job: refresh reference lists and the symbol master.

See idx_agent_infrastructure_diagrams_md.md section 7 and
sectors_idx_ingest_cache_plan_md.md section 2 (Helper lists, Screener symbol master).
"""

from data.cache import Cache
from data.db import Database
from data.sectors_client import SectorsClient

SCREENER_PAGE_SIZE = 200

REFERENCE_LISTS = {
    "subsectors": lambda client: client.get_subsectors(),
    "industries": lambda client: client.get_industries(),
    "subindustries": lambda client: client.get_subindustries(),
    "news_tags": lambda client: client.get_news_tags(),
    "broker_registry": lambda client: client.get_broker_registry(),
    "companies_with_revenue_segments": lambda client: client.get_companies_with_revenue_segments(),
}


def _row_to_symbol_master(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "name": row.get("company_name"),
        "sector": row.get("sector"),
        "subsector": row.get("subsector"),
        "industry": row.get("industry"),
        "subindustry": row.get("subindustry"),
        "listing_date": row.get("listing_date"),
    }


def sweep_symbol_master(client: SectorsClient) -> list[dict]:
    """One sweep of the screener with limit=200, paging by offset.

    TODO: confirm an empty `where` is accepted (plan doc section 8).
    """
    rows: list[dict] = []
    offset = 0
    while True:
        response = client.get_screener(where={}, limit=SCREENER_PAGE_SIZE, offset=offset)
        page = response.get("data", response)
        if not page:
            break
        rows.extend(page)
        if len(page) < SCREENER_PAGE_SIZE:
            break
        offset += SCREENER_PAGE_SIZE
    return rows


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    for name, fetch in REFERENCE_LISTS.items():
        db.upsert_reference_list(name, fetch(client))

    rows = sweep_symbol_master(client)
    db.upsert_symbol_master([_row_to_symbol_master(r) for r in rows])
