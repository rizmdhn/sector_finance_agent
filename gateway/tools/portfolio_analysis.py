"""Portfolio/liquidity/returns/fundamentals tools backed by analysis/ +
data/analysis_bridge.py.

analyze_portfolio/analyze_liquidity/analyze_returns read only already-ingested
Postgres data (never call SectorsClient) — zero Sectors API credits. analyze_
fundamentals fetches the company report's financials section (CACHE strategy): 1
credit per section on the first call for a symbol, free after that. See
data/analysis_bridge.py's module docstring.
"""

from typing import Literal

from strands import tool

from data import analysis_bridge
from data.deps import get_cache, get_client, get_db

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
def analyze_liquidity(
    symbol: str, position_value: float, participation_rate: float = 0.1, position_shares: float | None = None
) -> dict:
    """Estimate a position's normal-conditions liquidity: ADV20 (20-session median
    traded value) and the number of sessions to exit at a given participation rate.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        position_value: Value of the position to be exited.
        participation_rate: Fraction of ADV20 assumed executable per session.
        position_shares: Share count for the same position — pass this too to also
            get free_float_capacity (position shares / free-float shares). Costs 1
            Sectors credit the first time for this symbol (shares outstanding lookup),
            free after that; omit for the usual zero-credit liquidity-only result.
    """
    if position_shares is None:
        return analysis_bridge.liquidity_snapshot(get_db(), symbol, position_value, participation_rate)
    return analysis_bridge.liquidity_snapshot(
        get_db(),
        symbol,
        position_value,
        participation_rate,
        position_shares=position_shares,
        cache=get_cache(),
        client=get_client(),
    )


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


@tool
def analyze_fundamentals(symbol: str) -> dict:
    """Calculate fundamental ratios (margins, ROA/ROE, leverage, bank-specific
    ratios when applicable) and raw-component valuation (P/E, P/B, EV/EBITDA,
    FCFF/FCFE) for an IDX-listed company from its latest reported fiscal year.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
    """
    return analysis_bridge.fundamentals_snapshot(get_cache(), get_db(), get_client(), symbol)
