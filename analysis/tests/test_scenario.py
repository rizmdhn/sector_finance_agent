from analysis import scenario
from analysis.types import UNAVAILABLE


def test_scenario_portfolio_return():
    weights = {"A": 0.6, "B": 0.4}
    scenario_returns = {"A": -0.25, "B": -0.10}
    result = scenario.scenario_portfolio_return(weights, scenario_returns)
    assert round(result, 6) == round(0.6 * -0.25 + 0.4 * -0.10, 6)


def test_scenario_portfolio_return_blocked_by_missing_return():
    weights = {"A": 0.6, "B": 0.4}
    scenario_returns = {"A": -0.25}
    assert scenario.scenario_portfolio_return(weights, scenario_returns) is UNAVAILABLE


def test_scenario_loss_fraction():
    assert scenario.scenario_loss_fraction(-0.18) == 0.18


def test_scenario_currency_pnl():
    assert scenario.scenario_currency_pnl(1_000_000, -0.045) == -45_000
