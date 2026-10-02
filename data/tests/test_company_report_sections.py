"""An agent asked for cash_flow/balance_sheet/risk (found live) — none exist, the API
400s. The repository must reject unknown sections before touching db/cache/client,
and the tool's schema must offer the model only the real ones."""

import pytest

from data.repositories import COMPANY_REPORT_SECTIONS, get_company_report


def test_unknown_sections_rejected_before_any_dependency_is_touched():
    # None for db/cache/client: any attempt to use them would raise AttributeError
    with pytest.raises(ValueError) as exc:
        get_company_report(None, None, None, "BBCA", ["financials", "cash_flow", "risk"])

    message = str(exc.value)
    assert "cash_flow, risk" in message  # names exactly the bad ones
    assert all(section in message for section in COMPANY_REPORT_SECTIONS)  # and lists the valid ones
    assert "inside `financials`" in message


def test_tool_schema_enumerates_the_valid_sections():
    from gateway.tools.company_report import get_company_report as tool

    items = tool.tool_spec["inputSchema"]["json"]["properties"]["sections"]["items"]
    assert set(items["enum"]) == set(COMPANY_REPORT_SECTIONS)
