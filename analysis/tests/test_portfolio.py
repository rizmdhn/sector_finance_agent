import pytest

from analysis import portfolio
from analysis.types import UNAVAILABLE


def test_position_value():
    assert portfolio.position_value(100, 50) == 5000
    assert portfolio.position_value(100, UNAVAILABLE) is UNAVAILABLE


def test_portfolio_value_sums_positions_and_cash():
    assert portfolio.portfolio_value([1000, 2000], 500) == 3500


def test_portfolio_value_blocked_by_missing_position():
    assert portfolio.portfolio_value([1000, UNAVAILABLE], 500) is UNAVAILABLE


def test_portfolio_value_must_be_positive():
    with pytest.raises(ValueError):
        portfolio.portfolio_value([-1000], 500)


def test_weight():
    assert portfolio.weight(1000, 4000) == 0.25
    assert portfolio.weight(UNAVAILABLE, 4000) is UNAVAILABLE


def test_category_exposure_treats_unmapped_as_zero_but_flags_it():
    weights = {"A": 0.6, "B": 0.4}
    loadings = {"A": 1.0}  # B unmapped
    assert portfolio.category_exposure(weights, loadings) == 0.6
    assert portfolio.unmapped_symbols(weights, loadings) == {"B"}


def test_hhi_and_effective_number_of_holdings():
    weights = [0.5, 0.5]
    h = portfolio.hhi(weights)
    assert h == 0.5
    assert portfolio.effective_number_of_holdings(h) == 2


def test_effective_number_of_holdings_unavailable_for_zero_hhi():
    assert portfolio.effective_number_of_holdings(0) is UNAVAILABLE
