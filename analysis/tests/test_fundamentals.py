from analysis import fundamentals
from analysis.types import NM, UNAVAILABLE


def test_revenue_growth():
    assert round(fundamentals.revenue_growth(110, 100), 6) == 0.1


def test_roe_negative_equity_is_nm_not_unavailable():
    assert fundamentals.roe(100, -500) is NM


def test_roe_normal_case():
    assert round(fundamentals.roe(100, 1000), 6) == 0.1


def test_net_debt_to_ebitda_nonpositive_ebitda_is_nm():
    assert fundamentals.net_debt_to_ebitda(500, 0) is NM
    assert fundamentals.net_debt_to_ebitda(500, -10) is NM


def test_net_debt_to_ebitda_normal_case():
    assert fundamentals.net_debt_to_ebitda(500, 250) == 2


def test_interest_coverage_requires_positive_interest_expense():
    assert fundamentals.interest_coverage(1000, 0) is NM
    assert fundamentals.interest_coverage(1000, -5) is NM
    assert fundamentals.interest_coverage(1000, 100) == 10


def test_cash_conversion_nonpositive_profit_is_nm():
    assert fundamentals.cash_conversion(500, 0) is NM
    assert fundamentals.cash_conversion(500, -100) is NM


def test_cash_conversion_normal_case():
    assert fundamentals.cash_conversion(500, 250) == 2


def test_missing_inputs_are_unavailable_not_nm():
    assert fundamentals.roe(UNAVAILABLE, 1000) is UNAVAILABLE
    assert fundamentals.net_debt_to_ebitda(UNAVAILABLE, 250) is UNAVAILABLE


def test_bank_ratios():
    assert round(fundamentals.nim(50, 1000), 6) == 0.05
    assert round(fundamentals.gross_npl_ratio(20, 1000), 6) == 0.02
    assert fundamentals.loan_loss_coverage(30, 0) is UNAVAILABLE
    assert round(fundamentals.loan_to_deposit(800, 1000), 6) == 0.8
    assert round(fundamentals.cost_to_income(400, 1000), 6) == 0.4
    assert round(fundamentals.capital_adequacy(150, 1000), 6) == 0.15
