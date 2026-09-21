"""MVP tool: get_price_history. See idx_agent_infrastructure_diagrams_md.md section 9."""

from typing import Literal

from strands import tool

from data import repositories
from data.deps import get_db

Period = Literal["1m", "3m", "1y"]


@tool
def get_price_history(symbol: str, period: Period) -> list[dict]:
    """Get historical price data for an IDX-listed symbol.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        period: Preset lookback window.
    """
    return repositories.get_price_history(get_db(), symbol, period)
