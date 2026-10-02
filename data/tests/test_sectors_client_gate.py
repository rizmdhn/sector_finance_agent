"""SectorsClient must ask the installed gate BEFORE the HTTP request, make no request
when it's denied, and behave exactly as before with no gate installed."""

import pytest

from data import credit_gate
from data.credit_gate import SectorsCallDenied
from data.sectors_client import SectorsClient


class _Resp:
    status_code = 200

    def json(self):
        return {}

    def raise_for_status(self):
        pass


class _Gate:
    def __init__(self, allow):
        self.allow, self.asked = allow, []

    def check(self, description, credits):
        self.asked.append((description, credits))
        if not self.allow:
            raise SectorsCallDenied("no")


@pytest.fixture()
def client():
    c = SectorsClient(api_key="x", base_url="https://example.invalid/v2/")
    c.http_calls = 0

    def fake_get(path, params=None):
        c.http_calls += 1
        return _Resp()

    c._http.get = fake_get
    yield c
    credit_gate.set_gate(None)


def test_denied_call_never_reaches_the_network(client):
    credit_gate.set_gate(_Gate(allow=False))
    with pytest.raises(SectorsCallDenied):
        client._get("company_report", {"symbol": "BBCA.JK"}, sections="financials,overview")
    assert client.http_calls == 0


def test_approved_call_goes_through_and_reports_what_and_how_much(client):
    gate = _Gate(allow=True)
    credit_gate.set_gate(gate)
    client._get("company_report", {"symbol": "BBCA.JK"}, sections="financials,overview")

    assert client.http_calls == 1
    # a company report is 1 credit per section
    assert gate.asked == [("company/report/BBCA.JK/ (sections=financials,overview)", 2)]


def test_other_endpoints_cost_one_and_hide_paging_params(client):
    gate = _Gate(allow=True)
    credit_gate.set_gate(gate)
    client._get("screener", where="indices in ['lq45']", order_by="symbol", limit=200, offset=0)

    assert gate.asked == [("companies/ (where=indices in ['lq45'])", 1)]


def test_no_gate_installed_means_no_change(client):
    client._get("company_report", {"symbol": "BBCA.JK"}, sections="financials")
    assert client.http_calls == 1
