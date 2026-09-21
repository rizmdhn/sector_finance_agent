"""MVP tool: get_company_report. See idx_agent_infrastructure_diagrams_md.md section 9."""

from strands import tool

from data import repositories
from data.deps import get_cache, get_client, get_db


@tool
def get_company_report(symbol: str, sections: list[str]) -> dict:
    """Get a company report for an IDX-listed symbol.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        sections: Report sections to include.
    """
    return repositories.get_company_report(get_cache(), get_db(), get_client(), symbol, sections)
