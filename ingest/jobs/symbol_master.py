"""Weekly job: refresh reference lists and the symbol master.

See idx_agent_infrastructure_diagrams_md.md section 7 and
sectors_idx_ingest_cache_plan_md.md section 2 (Helper lists, Screener symbol master).

`companies_with_revenue_segments` was dropped from REFERENCE_LISTS: no working
endpoint path was found for it despite direct probing against the live API (see
data/sectors_client.py's module docstring) — revenue segments are unavailable until
that's resolved.
"""

from data.cache import Cache
from data.db import Database
from data.sectors_client import SectorsClient

SCREENER_PAGE_SIZE = 200

# An always-true compound condition referencing all four category fields, purely to
# force them into each row's `query_values` — confirmed live: `include_query_values`
# only returns fields actually referenced in `where`/`order_by`, not a full field
# dump, so a bare sweep with no `where` would come back with just symbol/company_name.
_CATEGORY_FIELDS_WHERE = "sector!='' and sub_sector!='' and industry!='' and sub_industry!=''"

REFERENCE_LISTS = {
    "subsectors": lambda client: client.get_subsectors(),
    "industries": lambda client: client.get_industries(),
    "subindustries": lambda client: client.get_subindustries(),
    "news_tags": lambda client: client.get_news_tags(),
    "broker_registry": lambda client: client.get_broker_registry(),
    # Whole-market, one row per symbol {"symbol", "company_name", "free_float"} —
    # confirmed live (2026-09-29), see data/repositories.py::get_free_float for the
    # per-symbol lookup this feeds. Backs analysis/liquidity.py's free_float_capacity.
    "free_float": lambda client: client.get_free_float(),
}


def _row_to_symbol_master(row: dict) -> dict:
    values = row.get("query_values", {})
    return {
        "symbol": row["symbol"],
        "name": row.get("company_name"),
        "sector": values.get("sector"),
        "subsector": values.get("sub_sector"),
        "industry": values.get("industry"),
        "subindustry": values.get("sub_industry"),
        # TODO: no confirmed field name for listing date via the screener; leaving
        # unset until that's found (not blocking — nothing depends on it yet).
        "listing_date": None,
    }


def sweep_symbol_master(client: SectorsClient) -> list[dict]:
    """One sweep of the screener with limit=200, paging via the response's own
    `pagination.has_next`/`next_offset` (confirmed live, see data/sectors_client.py)."""
    rows: list[dict] = []
    offset = 0
    while True:
        response = client.get_screener(
            where=_CATEGORY_FIELDS_WHERE, order_by="symbol", limit=SCREENER_PAGE_SIZE, offset=offset
        )
        rows.extend(response.get("results", []))
        pagination = response.get("pagination", {})
        if not pagination.get("has_next"):
            break
        offset = pagination.get("next_offset", offset + SCREENER_PAGE_SIZE)
    return rows


def run_essential(db: Database, client: SectorsClient) -> None:
    """Just the symbol master sweep — what `data/repositories.py::ensure_valid_symbol`
    actually needs to stop rejecting every ticker as "unknown". Used for a fresh
    install's first-boot bootstrap (ingest/scheduler.py): the full `run()` below adds
    6 more calls for REFERENCE_LISTS (subsectors/industries/.../free_float) that back
    screener filters and liquidity capacity — real features, but not required for a
    first chat message to work, and every extra call on that first burst is extra risk
    of the 429 found live (2026-10-01) combined with universe_close's own backfill
    burst. Those lists still land on the normal Monday 3am sweep via `run()`."""
    rows = sweep_symbol_master(client)
    db.upsert_symbol_master([_row_to_symbol_master(r) for r in rows])


def run(db: Database, cache: Cache, client: SectorsClient) -> None:
    for name, fetch in REFERENCE_LISTS.items():
        db.upsert_reference_list(name, fetch(client))

    run_essential(db, client)
