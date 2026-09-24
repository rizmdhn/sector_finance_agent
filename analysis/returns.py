"""Returns and drawdowns.
Appendix A, "Returns and drawdowns" (portfolio-intelligence-business-requirements-v1.1.md).
"""

import math
import statistics
from collections.abc import Sequence

from analysis.types import UNAVAILABLE, Number, is_missing


def simple_return(price_t: Number, price_t_minus_1: Number) -> Number:
    """Label the result a price return, not a total return, when dividends are
    unavailable — a caller-side labeling concern, not enforced here."""
    if is_missing(price_t) or is_missing(price_t_minus_1) or price_t_minus_1 == 0:
        return UNAVAILABLE
    return price_t / price_t_minus_1 - 1


def portfolio_return(beginning_weights: dict[str, float], period_returns: dict[str, Number]) -> Number:
    """Beginning-of-period weights apply to the corresponding return interval;
    intraperiod transactions need subperiod treatment by the caller."""
    total = 0.0
    for symbol, w in beginning_weights.items():
        r = period_returns.get(symbol, UNAVAILABLE)
        if is_missing(r):
            return UNAVAILABLE
        total += w * r
    return total


def annualized_volatility(period_returns: Sequence[float], periods_per_year: int) -> Number:
    if len(period_returns) < 2:
        return UNAVAILABLE
    return statistics.stdev(period_returns) * math.sqrt(periods_per_year)


def cash_flow_adjusted_wealth_index(
    cash_flow_adjusted_returns: Sequence[float], start: float = 1.0
) -> list[float]:
    """Use a cash-flow-adjusted return series (deposits/withdrawals excluded) so
    portfolio drawdown reflects performance only, per Appendix A."""
    index = start
    result = []
    for r in cash_flow_adjusted_returns:
        index *= 1 + r
        result.append(index)
    return result


def drawdown_series(wealth_index: Sequence[float]) -> list[Number]:
    if not wealth_index:
        return []
    peak = wealth_index[0]
    result = []
    for v in wealth_index:
        peak = max(peak, v)
        result.append(v / peak - 1 if peak > 0 else UNAVAILABLE)
    return result


def max_drawdown(wealth_index: Sequence[float]) -> Number:
    dd = [d for d in drawdown_series(wealth_index) if not is_missing(d)]
    if not dd:
        return UNAVAILABLE
    return min(dd)
