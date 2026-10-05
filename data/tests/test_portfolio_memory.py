"""Portfolio memory: replace-not-append, trades merge, can't sell what isn't saved."""

import pytest

from data import portfolio_memory


class _DB:
    def __init__(self):
        self.rows = []  # newest last; each {"content", "metadata", "active"}

    def is_valid_symbol(self, symbol):
        return symbol in {"BBCA.JK", "BMRI.JK", "TLKM.JK"}

    def get_active_portfolio(self, user_id):
        live = [r for r in self.rows if r["active"]]
        return live[-1] if live else None

    def replace_portfolio(self, user_id, content, metadata):
        for r in self.rows:
            r["active"] = False
        self.rows.append({"content": content, "metadata": metadata, "active": True})


def test_setting_again_replaces_instead_of_piling_up():
    db = _DB()
    portfolio_memory.set_portfolio(db, "u", {"bbca": 100})
    portfolio_memory.set_portfolio(db, "u", {"BBCA": 150, "TLKM": 40}, cash=1_000_000)

    assert sum(r["active"] for r in db.rows) == 1
    now = portfolio_memory.get_portfolio(db, "u")
    assert now["positions"] == {"BBCA.JK": 150.0, "TLKM.JK": 40.0} and now["cash"] == 1_000_000
    assert "BBCA.JK 150 shares" in db.rows[-1]["content"]


def test_trades_merge_into_the_saved_portfolio():
    db = _DB()
    portfolio_memory.set_portfolio(db, "u", {"BBCA": 100, "TLKM": 50}, cash=500)

    portfolio_memory.record_trades(db, "u", {"BBCA": 50, "TLKM": -50, "BMRI": 10})

    now = portfolio_memory.get_portfolio(db, "u")
    assert now["positions"] == {"BBCA.JK": 150.0, "BMRI.JK": 10.0}  # sold-out TLKM dropped
    assert now["cash"] == 500  # a trade doesn't guess a price, so cash is untouched


def test_cannot_sell_more_than_saved_and_nothing_changes():
    db = _DB()
    portfolio_memory.set_portfolio(db, "u", {"BBCA": 100})
    with pytest.raises(ValueError, match="only 100"):
        portfolio_memory.record_trades(db, "u", {"BBCA": -500})
    assert portfolio_memory.get_portfolio(db, "u")["positions"] == {"BBCA.JK": 100.0}


def test_unknown_ticker_is_rejected_before_anything_is_saved():
    db = _DB()
    with pytest.raises(Exception):
        portfolio_memory.set_portfolio(db, "u", {"NOPE": 10})
    assert db.rows == []
