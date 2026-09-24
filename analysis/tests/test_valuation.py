import pytest

from analysis import valuation
from analysis.types import NM, UNAVAILABLE, InvalidValuationAssumption


def test_pe_negative_earnings_is_nm():
    assert valuation.pe(1000, -50) is NM
    assert valuation.pe(1000, 0) is NM


def test_pe_normal_case():
    assert valuation.pe(1000, 100) == 10


def test_pb_negative_book_equity_is_nm():
    assert valuation.pb(1000, -50) is NM


def test_enterprise_value():
    ev = valuation.enterprise_value(
        common_equity_market_value=1000, debt=200, preferred_equity=0,
        noncontrolling_interests=50, cash=100,
    )
    assert ev == 1150


def test_ev_to_ebitda_nonpositive_ebitda_is_nm():
    assert valuation.ev_to_ebitda(1000, 0) is NM


def test_fcff():
    result = valuation.fcff(
        ebit=1000, tax_rate=0.25, depreciation_amortization=100, capex=150,
        change_in_operating_nwc=20,
    )
    assert round(result, 6) == 1000 * 0.75 + 100 - 150 - 20


def test_fcfe():
    result = valuation.fcfe(
        net_income=800, depreciation_amortization=100, capex=150,
        change_in_operating_nwc=20, net_borrowing=30,
    )
    assert result == 800 + 100 - 150 - 20 + 30


def test_terminal_value_fcff_rejects_when_wacc_does_not_exceed_g():
    with pytest.raises(InvalidValuationAssumption):
        valuation.terminal_value_fcff(100, wacc=0.08, g=0.08)
    with pytest.raises(InvalidValuationAssumption):
        valuation.terminal_value_fcff(100, wacc=0.05, g=0.08)


def test_terminal_value_fcff_normal_case():
    assert valuation.terminal_value_fcff(100, wacc=0.10, g=0.02) == 100 / 0.08


def test_terminal_value_fcfe_rejects_when_cost_of_equity_does_not_exceed_g():
    with pytest.raises(InvalidValuationAssumption):
        valuation.terminal_value_fcfe(100, cost_of_equity=0.08, g=0.10)


def test_missing_inputs_propagate_unavailable():
    assert valuation.pe(UNAVAILABLE, 100) is UNAVAILABLE
    assert valuation.fcff(UNAVAILABLE, 0.25, 100, 150, 20) is UNAVAILABLE
