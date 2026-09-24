"""Liquidity: ADV20, exit days, free-float capacity.
Appendix A, "Liquidity" (portfolio-intelligence-business-requirements-v1.1.md).

`ADV20` here means median daily traded value over 20 scheduled sessions, despite the
common use of "average" in that abbreviation — label it "20-session median traded
value" in any user-facing output.
"""

import statistics
from collections.abc import Sequence

from analysis.types import UNAVAILABLE, Number, is_missing


def adv20(daily_traded_values: Sequence[float | None]) -> Number:
    """`daily_traded_values` must include genuine zero-trading sessions as `0.0`.
    Use `None` for a session with no observation — never silently reuse an older
    active day's value to fill a gap. A suspended stock should be passed as entirely
    unavailable (e.g. all `None`) rather than computed from stale pre-suspension
    data, per Appendix A: "a suspended stock has no currently executable exit
    estimate."
    """
    if not daily_traded_values or any(v is None for v in daily_traded_values):
        return UNAVAILABLE
    return statistics.median(daily_traded_values)


def normal_exit_days(position_value: Number, adv20_: Number, participation_rate: float) -> Number:
    """A zero denominator (including adv20_ == 0) means the exit estimate is
    unavailable, not zero days."""
    if is_missing(position_value) or is_missing(adv20_):
        return UNAVAILABLE
    denominator = adv20_ * participation_rate
    if denominator == 0:
        return UNAVAILABLE
    return position_value / denominator


def stressed_traded_value(adv20_: Number, retained_liquidity_fraction: float) -> Number:
    if is_missing(adv20_):
        return UNAVAILABLE
    return adv20_ * retained_liquidity_fraction


def stressed_exit_days(
    position_value: Number, stressed_traded_value_: Number, participation_rate: float
) -> Number:
    if is_missing(position_value) or is_missing(stressed_traded_value_):
        return UNAVAILABLE
    denominator = stressed_traded_value_ * participation_rate
    if denominator == 0:
        return UNAVAILABLE
    return position_value / denominator


def free_float_capacity(position_shares: float, free_float_shares: Number) -> Number:
    if is_missing(free_float_shares) or free_float_shares == 0:
        return UNAVAILABLE
    return position_shares / free_float_shares
