"""Real bug found live (2026-10-01): a 429 response used to fall through to
`response.raise_for_status()`, an unhandled httpx.HTTPStatusError that crashed
whichever ingest job (or the whole worker process, on a fresh install's bootstrap
burst) hit it. SectorsClient must raise the dedicated SectorsRateLimitError
instead, so callers can catch it specifically."""

from data.sectors_client import SectorsClient, SectorsRateLimitError


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = "rate limited"

    def raise_for_status(self):
        raise AssertionError("should not reach raise_for_status on a 429")

    def json(self):
        return {}


def test_get_raises_rate_limit_error_on_429():
    client = SectorsClient(api_key="x", base_url="https://example.invalid/v2/")
    client._http.get = lambda path, params=None: _FakeResponse(429)

    try:
        client._get("daily_universe_close", trade_date="2026-09-29")
        assert False, "expected SectorsRateLimitError"
    except SectorsRateLimitError as exc:
        assert exc.status_code == 429
