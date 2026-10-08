"""429 handling: a short wait-and-retry first (most 429s are a momentary burst), and only
then the dedicated SectorsRateLimitError, so callers can catch it instead of crashing."""

import pytest

from data.sectors_client import RATE_LIMIT_RETRY_DELAYS_SECONDS, SectorsClient, SectorsRateLimitError


class _FakeResponse:
    def __init__(self, status_code: int, retry_after=None):
        self.status_code = status_code
        self.text = "rate limited"
        self.headers = {"Retry-After": retry_after} if retry_after is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError("should not reach raise_for_status on a 429")

    def json(self):
        return {"ok": True}


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr("data.sectors_client.time.sleep", waits.append)
    return waits


def _client(responses):
    client = SectorsClient(api_key="x", base_url="https://example.invalid/v2/")
    queue = list(responses)
    client._http.get = lambda path, params=None: queue.pop(0)
    return client


def test_a_brief_429_is_retried_and_succeeds(slept):
    client = _client([_FakeResponse(429), _FakeResponse(200)])
    assert client._get("daily_universe_close", date="2026-09-29") == {"ok": True}
    assert slept == [RATE_LIMIT_RETRY_DELAYS_SECONDS[0]]


def test_retry_after_header_is_honoured_but_capped(slept):
    client = _client([_FakeResponse(429, "3"), _FakeResponse(429, "999"), _FakeResponse(200)])
    client._get("daily_universe_close", date="2026-09-29")
    assert slept == [3.0, 15]


def test_still_limited_after_retries_raises_rate_limit_error(slept):
    client = _client([_FakeResponse(429)] * (len(RATE_LIMIT_RETRY_DELAYS_SECONDS) + 1))
    with pytest.raises(SectorsRateLimitError) as exc:
        client._get("daily_universe_close", date="2026-09-29")
    assert exc.value.status_code == 429
    assert slept == list(RATE_LIMIT_RETRY_DELAYS_SECONDS)
