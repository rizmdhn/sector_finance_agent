"""Simple price-shock scenarios only.
Appendix A, "Scenario and statistical risk — when implemented"
(portfolio-intelligence-business-requirements-v1.1.md).

Only the fixed-weight scenario aggregation formulas are implemented here, matching
section 9's "Should, if data and time permit" tier ("simple price-shock scenarios").
Covariance, portfolio variance, beta, and VaR/ES are explicitly "Later" priority
(factor risk, VaR/ES are listed under "Later") and are deliberately not built yet —
they need a historical-covariance design (sample length, shrinkage, missing-data
treatment) that deserves its own pass rather than a rushed inclusion here.
"""

from analysis.types import UNAVAILABLE, Number, is_missing


def scenario_portfolio_return(weights: dict[str, float], scenario_returns: dict[str, Number]) -> Number:
    """Assumes fixed starting weights over the stated scenario horizon and
    consistently defined asset returns, per Appendix A."""
    total = 0.0
    for symbol, w in weights.items():
        r = scenario_returns.get(symbol, UNAVAILABLE)
        if is_missing(r):
            return UNAVAILABLE
        total += w * r
    return total


def scenario_loss_fraction(scenario_portfolio_return_: Number) -> Number:
    if is_missing(scenario_portfolio_return_):
        return UNAVAILABLE
    return -scenario_portfolio_return_


def scenario_currency_pnl(portfolio_value_: Number, scenario_portfolio_return_: Number) -> Number:
    if is_missing(portfolio_value_) or is_missing(scenario_portfolio_return_):
        return UNAVAILABLE
    return portfolio_value_ * scenario_portfolio_return_
