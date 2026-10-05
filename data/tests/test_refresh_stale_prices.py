"""Stale-price refresh: fake db/cache/client, no network."""

from datetime import date, datetime

import pytest

from data import repositories
from data.credit_gate import SectorsCallDenied
from data.repositories import expected_last_close_date, refresh_stale_prices


class _DB:
    def __init__(self, latest):
        self.latest = latest  # symbol -> date

    def get_latest_close(self, symbol):
        return (self.latest[symbol], 100.0) if symbol in self.latest else None

    def is_valid_symbol(self, symbol):
        return True


class _Cache(dict):
    def set(self, key, value, ttl=None):
        self[key] = value


@pytest.fixture
def fetched(monkeypatch):
    calls = []

    def fake_detail(db, client, symbol):
        if symbol in client.declined:
            raise SectorsCallDenied("no")
        calls.append(symbol)

    monkeypatch.setattr(repositories, "ensure_price_detail", fake_detail)
    monkeypatch.setattr(repositories, "expected_last_close_date", lambda: date(2026, 10, 2))
    monkeypatch.setattr(repositories, "idx_today", lambda: date(2026, 10, 3))
    return calls


class _Client:
    declined = {"NO.JK"}


def test_only_stale_or_missing_symbols_are_fetched_once_a_day(fetched):
    db = _DB({"FRESH.JK": date(2026, 10, 2), "OLD.JK": date(2026, 9, 29)})
    cache = _Cache()

    out = refresh_stale_prices(db, cache, _Client(), ["FRESH.JK", "OLD.JK", "NEW.JK"])
    assert sorted(fetched) == ["NEW.JK", "OLD.JK"] and sorted(out["fetched"]) == ["NEW.JK", "OLD.JK"]

    refresh_stale_prices(db, cache, _Client(), ["OLD.JK", "NEW.JK"])  # same day: no second charge
    assert len(fetched) == 2


def test_declined_call_is_reported_and_retried_later(fetched):
    cache = _Cache()
    out = refresh_stale_prices(_DB({}), cache, _Client(), ["NO.JK"])
    assert out["failed"] == {"NO.JK": "you declined the call"} and not cache


def test_expected_close_is_previous_weekday_before_the_evening_window():
    tz = repositories.IDX_TIMEZONE
    assert expected_last_close_date(datetime(2026, 10, 5, 10, 0, tzinfo=tz)) == date(2026, 10, 2)  # Mon morning -> Fri
    assert expected_last_close_date(datetime(2026, 10, 6, 20, 0, tzinfo=tz)) == date(2026, 10, 6)  # Tue night -> Tue
    assert expected_last_close_date(datetime(2026, 10, 3, 20, 0, tzinfo=tz)) == date(2026, 10, 2)  # Saturday -> Fri
