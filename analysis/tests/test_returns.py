from analysis import returns
from analysis.types import UNAVAILABLE


def test_simple_return():
    assert round(returns.simple_return(110, 100), 6) == 0.1
    assert returns.simple_return(110, 0) is UNAVAILABLE
    assert returns.simple_return(UNAVAILABLE, 100) is UNAVAILABLE


def test_portfolio_return_uses_beginning_weights():
    weights = {"A": 0.5, "B": 0.5}
    period_returns = {"A": 0.1, "B": -0.1}
    result = returns.portfolio_return(weights, period_returns)
    assert round(result, 6) == 0.0


def test_portfolio_return_blocked_by_missing_return():
    weights = {"A": 0.5, "B": 0.5}
    period_returns = {"A": 0.1}
    assert returns.portfolio_return(weights, period_returns) is UNAVAILABLE


def test_annualized_volatility_needs_at_least_two_observations():
    assert returns.annualized_volatility([0.01], 252) is UNAVAILABLE
    vol = returns.annualized_volatility([0.01, -0.01, 0.02], 252)
    assert vol > 0


def test_cash_flow_adjusted_wealth_index_and_drawdown():
    wealth_index = returns.cash_flow_adjusted_wealth_index([0.1, -0.2, 0.05])
    assert wealth_index[0] == 1.1
    dd = returns.drawdown_series(wealth_index)
    # peak is wealth_index[0] == 1.1, trough after -20% move
    assert round(dd[1], 6) == round(wealth_index[1] / wealth_index[0] - 1, 6)


def test_max_drawdown():
    wealth_index = [1.0, 1.2, 0.9, 1.1]
    # peak 1.2, trough 0.9 -> drawdown = 0.9/1.2 - 1 = -0.25
    assert round(returns.max_drawdown(wealth_index), 6) == -0.25


def test_max_drawdown_empty_series_unavailable():
    assert returns.max_drawdown([]) is UNAVAILABLE
