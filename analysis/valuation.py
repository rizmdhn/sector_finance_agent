"""Valuation: multiples, enterprise value, FCFF/FCFE, terminal value.
Appendix A, "Valuation" (portfolio-intelligence-business-requirements-v1.1.md).

FCFF is discounted at WACC; FCFE is discounted at cost of equity — never the other
way round (see Appendix C's acceptance case rejecting that mix-up). The parameter
names below (`wacc` vs `cost_of_equity`) are deliberately distinct so a caller cannot
pass one where the other belongs without it being visible at the call site; full
enforcement is the calling agent's responsibility, not this module's.
"""

from analysis.types import NM, UNAVAILABLE, InvalidValuationAssumption, Number, is_missing


def pe(common_equity_market_value: Number, earnings_attributable: Number) -> Number:
    """Negative or near-zero earnings make ordinary P/E unsuitable -> NM."""
    if is_missing(common_equity_market_value) or is_missing(earnings_attributable):
        return UNAVAILABLE
    if earnings_attributable <= 0:
        return NM
    return common_equity_market_value / earnings_attributable


def pb(common_equity_market_value: Number, common_book_equity: Number) -> Number:
    """Negative book equity makes ordinary P/B unsuitable -> NM."""
    if is_missing(common_equity_market_value) or is_missing(common_book_equity):
        return UNAVAILABLE
    if common_book_equity <= 0:
        return NM
    return common_equity_market_value / common_book_equity


def enterprise_value(
    common_equity_market_value: Number,
    debt: Number,
    preferred_equity: Number,
    noncontrolling_interests: Number,
    cash: Number,
) -> Number:
    values = [common_equity_market_value, debt, preferred_equity, noncontrolling_interests, cash]
    if any(is_missing(v) for v in values):
        return UNAVAILABLE
    return common_equity_market_value + debt + preferred_equity + noncontrolling_interests - cash


def ev_to_ebitda(ev: Number, ebitda: Number) -> Number:
    if is_missing(ev) or is_missing(ebitda):
        return UNAVAILABLE
    if ebitda <= 0:
        return NM
    return ev / ebitda


def fcff(
    ebit: Number,
    tax_rate: Number,
    depreciation_amortization: Number,
    capex: Number,
    change_in_operating_nwc: Number,
) -> Number:
    values = [ebit, tax_rate, depreciation_amortization, capex, change_in_operating_nwc]
    if any(is_missing(v) for v in values):
        return UNAVAILABLE
    return ebit * (1 - tax_rate) + depreciation_amortization - capex - change_in_operating_nwc


def fcfe(
    net_income: Number,
    depreciation_amortization: Number,
    capex: Number,
    change_in_operating_nwc: Number,
    net_borrowing: Number,
) -> Number:
    values = [net_income, depreciation_amortization, capex, change_in_operating_nwc, net_borrowing]
    if any(is_missing(v) for v in values):
        return UNAVAILABLE
    return net_income + depreciation_amortization - capex - change_in_operating_nwc + net_borrowing


def terminal_value_fcff(fcff_n_plus_1: Number, wacc: float, g: float) -> Number:
    """Measured at the end of year n; the caller must still discount it back.
    Raises per Appendix C's acceptance case if the discount rate does not exceed g."""
    if is_missing(fcff_n_plus_1):
        return UNAVAILABLE
    if wacc <= g:
        raise InvalidValuationAssumption("WACC must exceed the terminal growth rate g")
    return fcff_n_plus_1 / (wacc - g)


def terminal_value_fcfe(fcfe_n_plus_1: Number, cost_of_equity: float, g: float) -> Number:
    """Measured at the end of year n; the caller must still discount it back.
    Raises per Appendix C's acceptance case if the discount rate does not exceed g."""
    if is_missing(fcfe_n_plus_1):
        return UNAVAILABLE
    if cost_of_equity <= g:
        raise InvalidValuationAssumption("cost of equity must exceed the terminal growth rate g")
    return fcfe_n_plus_1 / (cost_of_equity - g)
