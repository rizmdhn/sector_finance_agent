"""Pure logic — fake DB, constituents patched in, no network."""

from datetime import date

import pytest

from data import analysis_bridge
from data.repositories import get_index_constituents


class _FakeDB:
    def __init__(self, closes):
        self._closes = closes  # symbol -> [(date, close), ...]

    def latest_trade_date(self):
        return date(2026, 9, 28)

    def get_symbol_master(self):
        return [{"symbol": "UP.JK", "sector": "Consumer"}, {"symbol": "DOWN.JK", "sector": "Financials"}]

    def read_price_range(self, symbol, start, end):
        return [{"trade_date": d, "close": c} for d, c in self._closes.get(symbol, [])]


def test_index_snapshot_ranks_members_and_reports_gaps(monkeypatch):
    members = [{"symbol": s, "name": s} for s in ("UP.JK", "DOWN.JK", "FLAT.JK", "NODATA.JK", "ONEDAY.JK")]
    monkeypatch.setattr(analysis_bridge, "get_index_constituents", lambda *_a: members)
    db = _FakeDB(
        {
            "UP.JK": [(date(2026, 9, 1), 100), (date(2026, 9, 28), 110)],
            "DOWN.JK": [(date(2026, 9, 1), 100), (date(2026, 9, 28), 90)],
            "FLAT.JK": [(date(2026, 9, 2), 50), (date(2026, 9, 28), 50)],
            "ONEDAY.JK": [(date(2026, 9, 28), 70)],  # one session is not a return
        }
    )

    snap = analysis_bridge.index_snapshot(db, " LQ45 ", "1m")

    assert snap["index"] == "lq45"
    assert (snap["advancers"], snap["decliners"], snap["unchanged"]) == (1, 1, 1)
    assert snap["top_gainers"][0]["symbol"] == "UP.JK"
    assert snap["top_losers"][0]["symbol"] == "DOWN.JK"
    assert snap["missing_price_symbols"] == ["NODATA.JK", "ONEDAY.JK"]
    assert snap["average_price_return"] == pytest.approx(0.0)
    assert snap["top_gainers"][0]["sector"] == "Consumer"
    # sector comes from symbol_master; a member it doesn't know is "Unknown", not guessed
    assert list(snap["by_sector"]) == ["Consumer", "Unknown", "Financials"]
    assert snap["by_sector"]["Consumer"] == {"members": 1, "average_price_return": pytest.approx(0.1)}
    # window is what the data covers, not the period asked for
    assert snap["window"] == {"start": date(2026, 9, 1), "end": date(2026, 9, 28)}


def test_index_snapshot_with_no_price_data_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.setattr(analysis_bridge, "get_index_constituents", lambda *_a: [{"symbol": "X.JK", "name": "X"}])
    snap = analysis_bridge.index_snapshot(_FakeDB({}), "idx30", "1m")
    assert snap["with_price_data"] == 0 and snap["window"] is None
    assert snap["missing_price_symbols"] == ["X.JK"]


def test_unknown_period_rejected():
    with pytest.raises(ValueError, match="unknown period"):
        analysis_bridge.index_snapshot(_FakeDB({}), "lq45", "5y")


def test_not_loaded_is_an_error_not_an_empty_index():
    """A database from before the `indices` column existed has NULL everywhere until its
    next sweep — that must not read as "this index has no members"."""

    class _NotLoaded:
        def get_index_members(self, _code):
            return None

    with pytest.raises(RuntimeError, match="hasn't been loaded"):
        get_index_constituents(_NotLoaded(), "lq45")


def test_index_name_is_normalized_before_lookup():
    seen = []

    class _Db:
        def get_index_members(self, code):
            seen.append(code)
            return []

    get_index_constituents(_Db(), "  LQ45 ")
    assert seen == ["lq45"]
