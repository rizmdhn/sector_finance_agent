"""The Chief's tools for the user's saved holdings (data/portfolio_memory.py). Built per
request, closed over the user, so one user's portfolio is never visible to another."""

from strands import tool

from data import portfolio_memory
from data.db import Database


def build_portfolio_memory_tools(db: Database, user_id: str) -> list:
    @tool
    def get_portfolio() -> dict:
        """The user's saved holdings: shares per ticker, cash if known, and the date they
        were last updated. Call this before answering anything about "my portfolio" or
        before applying a trade. Returns {"portfolio": null} when nothing is saved."""
        return {"portfolio": portfolio_memory.get_portfolio(db, user_id)}

    @tool
    def set_portfolio(positions: dict[str, float], cash: float | None = None) -> dict:
        """Save the user's FULL current holdings, replacing what was saved before. Use it
        when they state what they hold ("I hold 1000 BBCA and 500 BMRI", "I now have 150
        BBCA"). List every ticker they own, in shares (1 lot = 100 shares).

        Args:
            positions: Map of IDX ticker to shares held.
            cash: Cash balance in rupiah, only if they gave it; omitted keeps the saved cash.
        """
        return portfolio_memory.set_portfolio(db, user_id, positions, cash)

    @tool
    def record_trades(trades: dict[str, float]) -> dict:
        """Apply changes to the saved holdings when the user says they bought or sold
        ("I bought 100 more BBCA", "sold all my TLKM"). Positive = bought, negative =
        sold, in shares (1 lot = 100 shares). For "sold half" or "sold all", call
        get_portfolio first and work out the share count. Fails if it would sell more
        than is saved: then ask the user for their real holdings and use set_portfolio.

        Args:
            trades: Map of IDX ticker to shares bought (+) or sold (-).
        """
        return portfolio_memory.record_trades(db, user_id, trades)

    return [get_portfolio, set_portfolio, record_trades]
