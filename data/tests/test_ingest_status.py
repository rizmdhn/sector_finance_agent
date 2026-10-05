from data import ingest_status
from data.sectors_client import SectorsAuthError


class _Cache:
    def __init__(self):
        self.data = {}

    def set(self, key, value, ttl=None):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)


def test_failure_is_dropped_once_the_sectors_key_changes(monkeypatch):
    cache = _Cache()
    monkeypatch.setenv("SECTORS_API_KEY", "old-key")
    ingest_status.record(cache, "universe_close", "failed", SectorsAuthError(401, "bad"))
    assert ingest_status.read(cache, "universe_close")["state"] == "failed"

    monkeypatch.setenv("SECTORS_API_KEY", "new-key")
    assert ingest_status.read(cache, "universe_close") is None  # banner clears without a Sectors call


def test_success_status_is_kept_across_key_changes(monkeypatch):
    cache = _Cache()
    monkeypatch.setenv("SECTORS_API_KEY", "old-key")
    ingest_status.record(cache, "universe_close", "ok")
    monkeypatch.setenv("SECTORS_API_KEY", "new-key")
    assert ingest_status.read(cache, "universe_close")["state"] == "ok"
