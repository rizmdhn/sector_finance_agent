"""Portfolio/liquidity/returns tools backed by analysis/ + data/analysis_bridge.py.

These read only already-ingested Postgres data (never call SectorsClient), so they
cost zero Sectors API credits — see data/analysis_bridge.py's module docstring.
"""

from typing import Literal

from strands import tool

from data import analysis_bridge
from data.deps import get_db

Period = Literal["1m", "3m", "1y"]


@tool
def analyze_portfolio(positions: dict[str, float], cash: float) -> dict:
    """Calculate portfolio value, position/weight breakdown, and concentration (HHI,
    effective number of holdings) for a set of IDX holdings.

    Args:
        positions: Map of IDX ticker (e.g. "BBCA") to shares held.
        cash: Cash balance in the same currency as the priced positions.
    """
    return analysis_bridge.portfolio_snapshot(get_db(), positions, cash)


@tool
def analyze_liquidity(symbol: str, position_value: float, participation_rate: float = 0.1) -> dict:
    """Estimate a position's normal-conditions liquidity: ADV20 (20-session median
    traded value) and the number of sessions to exit at a given participation rate.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        position_value: Value of the position to be exited.
        participation_rate: Fraction of ADV20 assumed executable per session.
    """
    return analysis_bridge.liquidity_snapshot(get_db(), symbol, position_value, participation_rate)


@tool
def analyze_returns(symbol: str, period: Period) -> dict:
    """Calculate day-over-day price returns and max drawdown for an IDX-listed
    symbol over a preset lookback window. Labeled as price returns, not total
    returns — dividend adjustment convention is unconfirmed.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        period: Preset lookback window.
    """
    return analysis_bridge.returns_snapshot(get_db(), symbol, period)
