"""Credit-safety of the symbol sweep: pages already paid for must never be re-bought,
and an empty sweep must fail loudly instead of "succeeding" with nothing written."""

import pytest

from ingest.jobs.symbol_master import _row_to_symbol_master, run_essential, sweep_symbol_master


class _MemCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value


class _PagedClient:
    """Two pages; `fail_page_2` makes the second page raise once (a 429 stand-in)."""

    def __init__(self, fail_page_2=False):
        self.calls = []
        self.fail_page_2 = fail_page_2

    def get_screener(self, where, order_by, limit, offset):
        self.calls.append(offset)
        if offset == 0:
            return {"results": [{"symbol": "AAAA.JK"}], "pagination": {"has_next": True, "next_offset": 200}}
        if self.fail_page_2:
            self.fail_page_2 = False
            raise RuntimeError("429")
        return {"results": [{"symbol": "BBBB.JK"}], "pagination": {"has_next": False}}


def test_retry_after_failure_does_not_rebuy_paid_pages():
    cache, client = _MemCache(), _PagedClient(fail_page_2=True)
    with pytest.raises(RuntimeError):
        sweep_symbol_master(client, cache)

    rows = sweep_symbol_master(client, cache)  # the retry

    assert [r["symbol"] for r in rows] == ["AAAA.JK", "BBBB.JK"]
    assert client.calls == [0, 200, 200]  # page 0 bought once; only the failed page repeated


def test_empty_sweep_raises_instead_of_writing_nothing():
    class _EmptyClient:
        def get_screener(self, **_kw):
            return {"results": [], "pagination": {"has_next": False}}

    with pytest.raises(RuntimeError, match="0 rows"):
        run_essential(db=None, client=_EmptyClient())


def test_row_carries_lowercase_indices_and_empty_list_for_non_members():
    member = {"symbol": "A.JK", "query_values": {"indices": ["LQ45", "IDX30"]}}
    outsider = {"symbol": "B.JK", "query_values": {"indices": []}}
    assert _row_to_symbol_master(member)["indices"] == ["lq45", "idx30"]
    # [] not None: NULL in symbol_master.indices means "never loaded"
    assert _row_to_symbol_master(outsider)["indices"] == []
