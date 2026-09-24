"""Portfolio value, exposure, and concentration.
Appendix A, "Portfolio value, exposure, and concentration"
(portfolio-intelligence-business-requirements-v1.1.md).
"""

from collections.abc import Iterable

from analysis.types import UNAVAILABLE, Number, is_missing


def position_value(shares: float, price: Number) -> Number:
    if is_missing(price):
        return UNAVAILABLE
    return shares * price


def portfolio_value(position_values: Iterable[Number], cash: float) -> Number:
    """Missing prices for material positions block full-portfolio weights, per
    Appendix A, unless an explicitly dated valuation assumption has been substituted
    by the caller before this is called. Total value must be positive."""
    values = list(position_values)
    if any(is_missing(v) for v in values):
        return UNAVAILABLE
    total = sum(values) + cash
    if total <= 0:
        raise ValueError("portfolio value must be positive")
    return total


def weight(position_value_: Number, portfolio_value_: Number) -> Number:
    if is_missing(position_value_) or is_missing(portfolio_value_):
        return UNAVAILABLE
    return position_value_ / portfolio_value_


def category_exposure(weights: dict[str, float], exposure_loadings: dict[str, float]) -> float:
    """Σ_i Weight_i × Exposure loading_i,k. A holding absent from `exposure_loadings`
    contributes zero here — use `unmapped_symbols` to surface that coverage gap
    separately, per Appendix A: "report the available issuer classifications and the
    coverage gap" rather than silently treating an unmapped holding as unexposed.
    """
    return sum(w * exposure_loadings.get(symbol, 0.0) for symbol, w in weights.items())


def unmapped_symbols(weights: dict[str, float], exposure_loadings: dict[str, float]) -> set[str]:
    return set(weights) - set(exposure_loadings)


def hhi(weights: Iterable[float]) -> float:
    """Caller must state whether `weights` includes cash or is equity-only
    renormalized to 100% — Appendix A: "do not compare the two conventions." Does
    not measure correlation or liquidity."""
    return sum(w**2 for w in weights)


def effective_number_of_holdings(hhi_value: float) -> Number:
    if hhi_value <= 0:
        return UNAVAILABLE
    return 1 / hhi_value
