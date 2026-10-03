"""Real bug found live (2026-10-01): a bootstrap job that hit a 429 used to wait
for its next NATURAL schedule (Monday 3am / the 16-19h WIB poll) before retrying —
on a fresh install that can be hours away. `_run_bootstrap_job` retries a few times
with a short backoff before giving up, instead of one-shot-then-wait-for-the-clock."""

from data.sectors_client import SectorsRateLimitError
from ingest.scheduler import BOOTSTRAP_RETRY_DELAYS_SECONDS, _run_bootstrap_job


def test_succeeds_without_retry_when_job_works(monkeypatch):
    monkeypatch.setattr("ingest.scheduler.time.sleep", lambda _s: None)
    calls = []
    _run_bootstrap_job("test", lambda: calls.append(1))
    assert calls == [1]


def test_retries_on_rate_limit_then_succeeds(monkeypatch):
    slept = []
    monkeypatch.setattr("ingest.scheduler.time.sleep", lambda s: slept.append(s))
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise SectorsRateLimitError(429, "rate limited")

    _run_bootstrap_job("test", flaky)

    assert attempts["n"] == 3
    assert slept == BOOTSTRAP_RETRY_DELAYS_SECONDS[:2]


def test_gives_up_after_exhausting_retries_without_raising(monkeypatch):
    monkeypatch.setattr("ingest.scheduler.time.sleep", lambda _s: None)

    def always_limited():
        raise SectorsRateLimitError(429, "rate limited")

    _run_bootstrap_job("test", always_limited)  # must not raise


def test_non_rate_limit_failure_is_logged_not_retried_or_raised(monkeypatch):
    """An empty sweep (or a DB error) won't fix itself on retry, and raising would
    crash-loop the worker — bootstrap logs it and moves on."""
    slept = []
    monkeypatch.setattr("ingest.scheduler.time.sleep", lambda s: slept.append(s))
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("symbol_master sweep returned 0 rows")

    _run_bootstrap_job("test", boom)  # must not raise

    assert calls == [1]
    assert slept == []


class _FakeCache:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ttl=None):
        self.data[key] = value


def test_failure_is_recorded_for_the_ui_with_a_readable_problem(monkeypatch):
    monkeypatch.setattr("ingest.scheduler.time.sleep", lambda _s: None)
    from data.sectors_client import SectorsAuthError

    cache = _FakeCache()

    def rejected():
        raise SectorsAuthError(401, "bad key")

    _run_bootstrap_job("symbol_master", rejected, cache)

    status = cache.data["ingest:status:symbol_master"]
    assert status["state"] == "failed" and status["problem"]["code"] == "sectors_key_invalid"
