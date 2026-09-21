"""MVP tool: get_market_movers. See idx_agent_infrastructure_diagrams_md.md section 9."""

from strands import tool

from data import repositories
from data.deps import get_cache, get_client


@tool
def get_market_movers(classification: str, period: str) -> list[dict]:
    """Get top market movers for a classification and period.

    Args:
        classification: e.g. "gainers", "losers", "most_active".
        period: Enum period, e.g. "1d", "1w".
    """
    return repositories.get_market_movers(get_cache(), get_client(), classification, period)
