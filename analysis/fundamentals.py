"""Fundamentals, including bank-specific measures.
Appendix A, "Fundamentals" (portfolio-intelligence-business-requirements-v1.1.md).

Normalized values must reconcile to reported values — a bookkeeping requirement for
whoever constructs the inputs, not something these pure functions can enforce.
"""

from analysis.types import NM, UNAVAILABLE, Number, is_missing


def revenue_growth(revenue_t: Number, revenue_comparable_prior: Number) -> Number:
    if is_missing(revenue_t) or is_missing(revenue_comparable_prior) or revenue_comparable_prior == 0:
        return UNAVAILABLE
    return revenue_t / revenue_comparable_prior - 1


def operating_margin(operating_profit: Number, revenue: Number) -> Number:
    if is_missing(operating_profit) or is_missing(revenue) or revenue == 0:
        return UNAVAILABLE
    return operating_profit / revenue


def net_margin(net_income: Number, revenue: Number) -> Number:
    if is_missing(net_income) or is_missing(revenue) or revenue == 0:
        return UNAVAILABLE
    return net_income / revenue


def roa(net_income: Number, average_total_assets: Number) -> Number:
    if is_missing(net_income) or is_missing(average_total_assets) or average_total_assets <= 0:
        return UNAVAILABLE
    return net_income / average_total_assets


def roe(net_income_attributable: Number, average_common_equity: Number) -> Number:
    """Negative equity makes ordinary ROE unsuitable -> NM, per Appendix A."""
    if is_missing(net_income_attributable) or is_missing(average_common_equity):
        return UNAVAILABLE
    if average_common_equity <= 0:
        return NM
    return net_income_attributable / average_common_equity


def net_debt(interest_bearing_debt: Number, cash_and_equivalents: Number) -> Number:
    if is_missing(interest_bearing_debt) or is_missing(cash_and_equivalents):
        return UNAVAILABLE
    return interest_bearing_debt - cash_and_equivalents


def net_debt_to_ebitda(net_debt_: Number, ebitda: Number) -> Number:
    """Nonpositive EBITDA makes this NM, per Appendix A."""
    if is_missing(net_debt_) or is_missing(ebitda):
        return UNAVAILABLE
    if ebitda <= 0:
        return NM
    return net_debt_ / ebitda


def interest_coverage(ebit: Number, interest_expense: Number) -> Number:
    """Requires positive, consistently defined interest expense, per Appendix A."""
    if is_missing(ebit) or is_missing(interest_expense):
        return UNAVAILABLE
    if interest_expense <= 0:
        return NM
    return ebit / interest_expense


def cfo_margin(operating_cash_flow: Number, revenue: Number) -> Number:
    if is_missing(operating_cash_flow) or is_missing(revenue) or revenue == 0:
        return UNAVAILABLE
    return operating_cash_flow / revenue


def cash_conversion(operating_cash_flow: Number, net_income: Number) -> Number:
    """Nonpositive profit makes this NM; discuss operating cash flow, cash burn,
    and funding needs instead, per Appendix A."""
    if is_missing(operating_cash_flow) or is_missing(net_income):
        return UNAVAILABLE
    if net_income <= 0:
        return NM
    return operating_cash_flow / net_income


# -- Bank-specific measures ---------------------------------------------------
# "Exact inclusions and regulatory definitions may differ. Do not recreate a
# reported bank ratio from incompatible components or present it as comparable
# when its convention is unknown" (Appendix A) — record the source convention
# alongside these values; that provenance tracking is the caller's responsibility.


def nim(net_interest_income: Number, average_earning_assets: Number) -> Number:
    if is_missing(net_interest_income) or is_missing(average_earning_assets) or average_earning_assets <= 0:
        return UNAVAILABLE
    return net_interest_income / average_earning_assets


def gross_npl_ratio(non_performing_loans: Number, gross_loans: Number) -> Number:
    if is_missing(non_performing_loans) or is_missing(gross_loans) or gross_loans <= 0:
        return UNAVAILABLE
    return non_performing_loans / gross_loans


def loan_loss_coverage(applicable_loan_loss_reserves: Number, non_performing_loans: Number) -> Number:
    if (
        is_missing(applicable_loan_loss_reserves)
        or is_missing(non_performing_loans)
        or non_performing_loans <= 0
    ):
        return UNAVAILABLE
    return applicable_loan_loss_reserves / non_performing_loans


def loan_to_deposit(loans: Number, deposits: Number) -> Number:
    if is_missing(loans) or is_missing(deposits) or deposits <= 0:
        return UNAVAILABLE
    return loans / deposits


def cost_to_income(operating_expense: Number, operating_income: Number) -> Number:
    if is_missing(operating_expense) or is_missing(operating_income) or operating_income <= 0:
        return UNAVAILABLE
    return operating_expense / operating_income


def capital_adequacy(eligible_regulatory_capital: Number, risk_weighted_assets: Number) -> Number:
    if is_missing(eligible_regulatory_capital) or is_missing(risk_weighted_assets) or risk_weighted_assets <= 0:
        return UNAVAILABLE
    return eligible_regulatory_capital / risk_weighted_assets
