from analysis import liquidity
from analysis.types import UNAVAILABLE


def test_adv20_median_including_zero_sessions():
    # genuine zero-trading sessions must count, not be dropped
    values = [100.0] * 19 + [0.0]
    assert liquidity.adv20(values) == 100.0


def test_adv20_unavailable_when_any_session_missing():
    values = [100.0] * 19 + [None]
    assert liquidity.adv20(values) is UNAVAILABLE


def test_adv20_unavailable_for_empty_series():
    assert liquidity.adv20([]) is UNAVAILABLE


def test_normal_exit_days():
    # position 1_000_000, adv20 500_000, participation 0.1 -> denom 50_000 -> 20 days
    assert liquidity.normal_exit_days(1_000_000, 500_000, 0.1) == 20


def test_normal_exit_days_zero_adv20_is_unavailable_not_zero():
    assert liquidity.normal_exit_days(1_000_000, 0, 0.1) is UNAVAILABLE


def test_stressed_traded_value_and_exit_days():
    stv = liquidity.stressed_traded_value(500_000, 0.5)
    assert stv == 250_000
    days = liquidity.stressed_exit_days(1_000_000, stv, 0.1)
    assert days == 40


def test_free_float_capacity():
    assert liquidity.free_float_capacity(1000, 10000) == 0.1


def test_free_float_capacity_zero_denominator_unavailable():
    assert liquidity.free_float_capacity(1000, 0) is UNAVAILABLE
