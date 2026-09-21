"""MVP tool: screen_companies. See idx_agent_infrastructure_diagrams_md.md section 9."""

from strands import tool

from data import repositories
from data.deps import get_cache, get_client


@tool
def screen_companies(where: dict, order_by: str, limit: int) -> list[dict]:
    """Screen IDX-listed companies against structured filters.

    Args:
        where: Field filters from the allowed field list.
        order_by: Field to sort by.
        limit: Max number of results.
    """
    return repositories.screen_companies(
        get_cache(), get_client(), where=where, order_by=order_by, limit=limit
    )
