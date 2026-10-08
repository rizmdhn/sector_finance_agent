"""Pure logic only — no network/DB, unlike data/tests' integration style."""

from datetime import date

from data.sectors_client import SectorsAPIError, SectorsRateLimitError
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
        self._latest = latest  # newest whole day stored before progress tracking existed
        self.upserted: list[dict] = []
        self.progress: dict = {}

    def latest_complete_close_date(self):
        done = [d for d, p in self.progress.items() if p["complete"]]
        candidates = [d for d in done + [self._latest] if d is not None]
        return max(candidates) if candidates else None

    def incomplete_close_dates(self):
        return sorted(d for d, p in self.progress.items() if not p["complete"])

    def get_close_progress(self, trade_date):
        return self.progress.get(trade_date)

    def save_close_progress(self, trade_date, next_offset, total_count, complete):
        self.progress[trade_date] = {"next_offset": next_offset, "total_count": total_count, "complete": complete}

    def upsert_price_rows(self, rows):
        self.upserted.extend(rows)


class _FakeCache:
    def __init__(self):
        self.bumped = 0
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ttl=None):
        self.data[key] = value

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


def test_run_treats_date_in_future_as_not_landed():
    """Real bug found live (2026-10-01): the API can 400 "Date cannot be in the
    future" for idx_today() itself rather than returning empty results — that
    must not crash the whole job, same as any other not-landed-yet date."""

    class _FutureRejectingClient(_FakeClient):
        def get_daily_universe_close(self, trade_date, offset=0):
            if trade_date == "2026-09-29":
                raise SectorsAPIError(400, '{"error":"Date cannot be in the future."}')
            return super().get_daily_universe_close(trade_date, offset)

    today = date(2026, 9, 29)
    db = _FakeDB(latest=date(2026, 9, 28))
    cache = _FakeCache()
    client = _FutureRejectingClient(landed_dates=set())

    run(db, cache, client, today=today)  # must not raise

    assert db.upserted == []
    assert cache.bumped == 0


def test_run_backfills_full_window_on_empty_database():
    """Real bug found live (2026-10-01): latest=None (a brand-new install) used
    to set start=today, so the very first run only ever tried today's date —
    silently leaving the database empty forever if that date wasn't available
    yet. A never-ingested database must get the same backfill window as a long
    outage, not a narrower one."""
    today = date(2026, 9, 30)
    db = _FakeDB(latest=None)
    cache = _FakeCache()
    client = _FakeClient(landed_dates={"2026-09-28", "2026-09-29", "2026-09-30"})

    run(db, cache, client, today=today)

    assert len(db.upserted) == 3  # all three landed dates actually got saved
    assert cache.bumped == 1


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


def test_run_honors_smaller_max_backfill_days_override():
    """`max_backfill_days` lets a caller ask for a lighter catch-up than the
    MAX_BACKFILL_DAYS default (e.g. scripts/manage.py, to control credit cost
    explicitly) — checks `run` actually honors the override rather than a smaller
    value silently being ignored. ingest/scheduler.py's normal cron poll doesn't
    need this at all (real cost feedback, 2026-10-01: an earlier version had it
    eagerly bootstrap a couple of days of price history on every fresh install;
    removed — price data isn't required for the platform to work, see
    gateway/main.py's get_readiness(), so spending credits on it unprompted
    fought this project's whole purpose of controlling Sectors credit spend)."""
    today = date(2026, 9, 29)
    db = _FakeDB(latest=None)
    cache = _FakeCache()
    client = _FakeClient(landed_dates=set())
    small_window = 2

    calls = []
    real_get = client.get_daily_universe_close
    client.get_daily_universe_close = lambda trade_date, offset=0: (calls.append(trade_date), real_get(trade_date, offset))[1]

    run(db, cache, client, today=today, max_backfill_days=small_window)

    assert len(calls) <= small_window + 1
    assert small_window < MAX_BACKFILL_DAYS


def test_run_propagates_rate_limit_error_uncaught():
    """A 429 partway through must surface to the caller (ingest/scheduler.py
    catches it specifically) rather than being silently swallowed here."""

    class _RateLimitedClient(_FakeClient):
        def get_daily_universe_close(self, trade_date, offset=0):
            raise SectorsRateLimitError(429, "rate limited")

    today = date(2026, 9, 29)
    db = _FakeDB(latest=None)
    cache = _FakeCache()
    client = _RateLimitedClient(landed_dates=set())

    try:
        run(db, cache, client, today=today, max_backfill_days=1)
        assert False, "expected SectorsRateLimitError to propagate"
    except SectorsRateLimitError:
        pass


def test_rate_limit_mid_day_keeps_pages_in_postgres_and_rerun_resumes():
    """Live (2026-10-08): a manual pull hit 429 part-way through a day and saved nothing.
    Each page must be written as it arrives, and the next run (manual or scheduled) must
    continue from the page that failed instead of buying the day again."""

    class _Paged(_FakeClient):
        def __init__(self):
            super().__init__(landed_dates={"2026-10-08"})
            self.calls, self.fail_at = [], 40

        def get_daily_universe_close(self, trade_date, offset=0):
            self.calls.append(offset)
            if offset == self.fail_at:
                raise SectorsRateLimitError(429, "slow down")
            row = {"symbol": f"S{offset}.JK", "date": trade_date, "close": 1}
            return {"results": [row] * 20, "pagination": {"has_next": offset < 60, "next_offset": offset + 20, "total_count": 80}}

    day = date(2026, 10, 8)
    db, cache, client = _FakeDB(latest=None), _FakeCache(), _Paged()
    try:
        run(db, cache, client, today=day, max_backfill_days=0)
        assert False, "expected SectorsRateLimitError"
    except SectorsRateLimitError as exc:
        assert "offset 40 of 80" in str(exc)
    assert len(db.upserted) == 40  # the two pages paid for are already in Postgres
    assert db.progress[day] == {"next_offset": 40, "total_count": 80, "complete": False}

    client.fail_at, client.calls = None, []
    run(db, cache, client, today=day, max_backfill_days=0)
    assert client.calls == [40, 60]  # resumed, pages 0 and 20 not bought again
    assert len(db.upserted) == 80 and db.progress[day]["complete"]

    client.calls = []
    run(db, cache, client, today=day, max_backfill_days=0)
    assert client.calls == []  # complete day: nothing more to buy


def test_a_cut_off_older_day_is_finished_before_new_days():
    db, cache = _FakeDB(latest=None), _FakeCache()
    db.save_close_progress(date(2026, 10, 7), 20, 40, complete=False)
    db.save_close_progress(date(2026, 10, 6), 40, 40, complete=True)
    calls = []

    class _C(_FakeClient):
        def get_daily_universe_close(self, trade_date, offset=0):
            calls.append((trade_date, offset))
            return {"results": [{"symbol": "X.JK", "date": trade_date, "close": 1}], "pagination": {"has_next": False}}

    run(db, cache, _C(landed_dates=set()), today=date(2026, 10, 8))
    assert calls == [("2026-10-07", 20), ("2026-10-08", 0)]
