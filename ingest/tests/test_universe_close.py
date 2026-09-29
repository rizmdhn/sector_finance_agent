"""Pure logic only — no network/DB, unlike data/tests' integration style."""

from datetime import date

from ingest.jobs.universe_close import MAX_BACKFILL_DAYS, _weekdays_between, run


def test_weekdays_between_skips_weekend():
    # Fri 2026-09-25 .. Mon 2026-09-28 should skip Sat/Sun.
    got = _weekdays_between(date(2026, 9, 25), date(2026, 9, 28))
    assert got == [date(2026, 9, 25), date(2026, 9, 28)]


def test_weekdays_between_single_day():
    d = date(2026, 9, 29)
    assert _weekdays_between(d, d) == [d]


class _FakeDB:
    def __init__(self, latest):
        self._latest = latest
        self.upserted: list[dict] = []

    def latest_trade_date(self):
        return self._latest

    def upsert_price_rows(self, rows):
        self.upserted.extend(rows)


class _FakeCache:
    def __init__(self):
        self.bumped = 0

    def bump_epoch(self, name):
        self.bumped += 1


class _FakeClient:
    def __init__(self, landed_dates: set[str]):
        self._landed = landed_dates

    def get_daily_universe_close(self, trade_date: str, offset: int = 0):
        if trade_date not in self._landed:
            return {"results": [], "pagination": {"has_next": False}}
        return {"results": [{"symbol": "BBCA.JK", "date": trade_date, "close": 6300}], "pagination": {"has_next": False}}


def test_run_backfills_missed_weekday_and_bumps_once():
    today = date(2026, 9, 29)  # Tuesday
    db = _FakeDB(latest=date(2026, 9, 25))  # last stored: Friday
    cache = _FakeCache()
    client = _FakeClient(landed_dates={"2026-09-28", "2026-09-29"})

    run(db, cache, client, today=today)

    assert len(db.upserted) == 2  # Mon + Tue landed; Sat/Sun skipped by _weekdays_between
    assert cache.bumped == 1  # bumped once, not once per date


def test_run_caps_backfill_depth():
    today = date(2026, 9, 29)
    very_old = date(2026, 1, 1)
    db = _FakeDB(latest=very_old)
    cache = _FakeCache()
    client = _FakeClient(landed_dates=set())  # nothing lands; just checking call count stays bounded

    calls = []
    real_get = client.get_daily_universe_close
    client.get_daily_universe_close = lambda trade_date, offset=0: (calls.append(trade_date), real_get(trade_date, offset))[1]

    run(db, cache, client, today=today)

    assert len(calls) <= MAX_BACKFILL_DAYS + 1  # +1 for weekday/weekend rounding slack
