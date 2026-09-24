from analysis import flow
from analysis.types import UNAVAILABLE


def test_normalized_foreign_flow():
    assert flow.normalized_foreign_flow(1_000_000, 500_000) == 2


def test_normalized_foreign_flow_zero_adv20_unavailable():
    assert flow.normalized_foreign_flow(1_000_000, 0) is UNAVAILABLE


def test_broker_gross_value():
    assert flow.broker_gross_value(600, 400) == 1000


def test_top_k_broker_gross_share():
    values = {"A": 500, "B": 300, "C": 200}
    # top-2 = A+B = 800, total = 1000 -> 0.8
    assert flow.top_k_broker_gross_share(values, k=2) == 0.8


def test_top_k_broker_gross_share_empty_is_unavailable():
    assert flow.top_k_broker_gross_share({}, k=2) is UNAVAILABLE


def test_broker_net_imbalance():
    buys = {"A": 600, "B": 100}
    sells = {"A": 400, "B": 100}
    # gross = (600+400) + (100+100) = 1200, imbalance = |600-400| + |100-100| = 200
    assert round(flow.broker_net_imbalance(buys, sells), 6) == round(200 / 1200, 6)


def test_broker_net_imbalance_no_brokers_unavailable():
    assert flow.broker_net_imbalance({}, {}) is UNAVAILABLE
