"""MVP tool: screen_companies. See idx_agent_infrastructure_diagrams_md.md section 9."""

from strands import tool

from data import repositories
from data.deps import get_cache, get_client


@tool
def screen_companies(where: str, order_by: str, limit: int) -> list[dict]:
    """Screen IDX-listed companies against structured filters.

    Args:
        where: A SQL-like condition string, e.g. "sector='Financials' and market_cap>1000000000000" (index
            membership is also filterable: "indices in ['lq45']").
        order_by: Field to sort by; prefix with "-" for descending, e.g. "-market_cap".
        limit: Max number of results.
    """
    return repositories.screen_companies(
        get_cache(), get_client(), where=where, order_by=order_by, limit=limit
    )
