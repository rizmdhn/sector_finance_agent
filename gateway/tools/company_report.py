"""MVP tool: get_company_report. See idx_agent_infrastructure_diagrams_md.md section 9."""

from typing import Literal

from strands import tool

from data import analysis_bridge, repositories
from data.deps import get_cache, get_client, get_db

# Derived from the one list the repository validates against, so the schema the model
# sees (an enum) can't drift from what the API actually accepts.
ReportSection = Literal[repositories.COMPANY_REPORT_SECTIONS]  # type: ignore[valid-type]


@tool
def get_company_report(symbol: str, sections: list[ReportSection]) -> dict:
    """Get a company report for an IDX-listed symbol. Only the sections listed below
    exist — there is no separate cash_flow, balance_sheet or risk section.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        sections: Which sections to include. Request only what the question needs.
            overview: company profile and headline figures. valuation: valuation
            multiples. future: forward-looking estimates. peers: peer comparison.
            financials: multi-year financial statements — income statement, balance
            sheet and cash-flow lines (operating/investing/financing cash flow, free
            cash flow, total assets/liabilities/equity, debt) plus EPS, ratios and
            growth. dividend: dividend history. management: management and board.
            ownership: ownership information.
    """
    return repositories.get_company_report(get_cache(), get_db(), get_client(), symbol, sections)


@tool
def analyze_ownership(symbol: str) -> dict:
    """Get ownership composition for an IDX-listed symbol: local/foreign holder
    breakdown by category, shareholder-count trend, and free float percentage.
    Does NOT include named major shareholders or a corporate-group/controlling-group
    mapping — no data source has that; say so if asked rather than guessing.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
    """
    return analysis_bridge.ownership_snapshot(get_cache(), get_db(), get_client(), symbol)
