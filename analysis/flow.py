"""Flow and broker activity.
Appendix A, "Flow and broker activity" (portfolio-intelligence-business-requirements-v1.1.md).

None of these measures establishes trading intent — that interpretive caveat belongs
in whatever narrates these numbers, not in the arithmetic itself.
"""

from analysis.types import UNAVAILABLE, Number, is_missing


def normalized_foreign_flow(net_foreign_traded_value: Number, adv20_: Number) -> Number:
    """Expressed in daily-traded-value equivalents, not as a percentage of window
    turnover, per Appendix A."""
    if is_missing(net_foreign_traded_value) or is_missing(adv20_) or adv20_ == 0:
        return UNAVAILABLE
    return net_foreign_traded_value / adv20_


def broker_gross_value(buy_value: Number, sell_value: Number) -> Number:
    if is_missing(buy_value) or is_missing(sell_value):
        return UNAVAILABLE
    return buy_value + sell_value


def top_k_broker_gross_share(broker_gross_values: dict[str, float], k: int) -> Number:
    """Use the same market, coverage, units, and time window across all brokers in
    `broker_gross_values`; partial broker coverage must be labeled by the caller."""
    if not broker_gross_values or k <= 0:
        return UNAVAILABLE
    total = sum(broker_gross_values.values())
    if total <= 0:
        return UNAVAILABLE
    top_k = sorted(broker_gross_values.values(), reverse=True)[:k]
    return sum(top_k) / total


def broker_net_imbalance(buy_values: dict[str, float], sell_values: dict[str, float]) -> Number:
    """Summing both sides counts each market trade twice when coverage is complete —
    do not divide this numerator by single-sided exchange turnover."""
    brokers = set(buy_values) | set(sell_values)
    if not brokers:
        return UNAVAILABLE
    gross_total = 0.0
    imbalance_total = 0.0
    for b in brokers:
        buy = buy_values.get(b, 0.0)
        sell = sell_values.get(b, 0.0)
        gross_total += buy + sell
        imbalance_total += abs(buy - sell)
    if gross_total <= 0:
        return UNAVAILABLE
    return imbalance_total / gross_total
