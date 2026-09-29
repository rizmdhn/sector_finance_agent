"""MVP tool: get_company_report. See idx_agent_infrastructure_diagrams_md.md section 9."""

from strands import tool

from data import analysis_bridge, repositories
from data.deps import get_cache, get_client, get_db


@tool
def get_company_report(symbol: str, sections: list[str]) -> dict:
    """Get a company report for an IDX-listed symbol.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        sections: Report sections to include.
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
