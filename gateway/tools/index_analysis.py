"""analyze_index: constituents of an IDX index (LQ45, IDX30, ...) plus how its members
moved. Membership lives in symbol_master.indices (filled by the weekly ticker sweep)
and the price moves read ingested prices — both Postgres-only, zero Sectors credits.
See data/analysis_bridge.py::index_snapshot."""

from typing import Literal

from strands import tool

from data import analysis_bridge
from data.deps import get_db

Period = Literal["1m", "3m", "1y"]


@tool
def analyze_index(index: str, period: Period = "1m") -> dict:
    """List the companies in an IDX index and show how they moved: advancers vs
    decliners, the top gainers and losers, and the equal-weighted average price
    return of the members. Use it for "which companies are in LQ45" and "what
    happened inside IDX30 this month".

    Args:
        index: Index name, lowercase slug. All 15 in the data: "lq45", "idx30", "kompas100",
            "idxq30", "idxv30", "idxg30", "idxhidiv20", "idxbumn20", "idxesgl", "idxvesta28",
            "jii70", "srikehati", "economic30", "sminfra18", "ftse". An unknown name returns
            no members rather than an error.
        period: Window to measure, "1m", "3m" or "1y" (default "1m").
    """
    return analysis_bridge.index_snapshot(get_db(), index, period)
