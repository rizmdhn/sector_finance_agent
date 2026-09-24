"""Market and Event Intelligence tools: corporate actions (per-symbol and
market-wide calendar), filings, news, foreign flow, per-symbol broker activity,
suspensions, and top-broker rankings. Thin wrappers over data/repositories.py's
CACHE-strategy functions (see that module for the TTL/credit rationale), which in
turn wrap endpoints confirmed to exist live against the real Sectors API —
data/sectors_client.py's module docstring flags that these endpoints' *filter query
parameters* (symbol/date) were only tested with zero params, not confirmed to
actually filter. Treat a suspiciously large or unfiltered-looking result as a sign
the filter may have been silently ignored, not necessarily a real finding.

Every real fetch (a Valkey cache miss) here also persists to Postgres — see
data/repositories.py's module docstring for why (real gap found live: nothing
durable recorded a news/filing/corporate-action/foreign-flow fetch before this).
"""

from datetime import date

from strands import tool

from data import repositories
from data.deps import get_cache, get_client, get_db


@tool
def get_corporate_actions(symbol: str) -> dict:
    """Corporate actions (dividends, splits, rights issues, etc.) for one
    IDX-listed company.

    Args:
        symbol: IDX ticker, e.g. "BBCA".
    """
    return repositories.get_corporate_actions(get_cache(), get_db(), get_client(), symbol)


@tool
def get_filings(symbol: str | None = None) -> dict:
    """Regulatory filings/disclosures, optionally filtered to one company.

    Args:
        symbol: IDX ticker to filter to, e.g. "BBCA". Omit for all recent filings.
    """
    return repositories.get_filings(get_cache(), get_db(), get_client(), symbol)


@tool
def get_news(symbol: str | None = None) -> dict:
    """News items, optionally filtered to one company.

    Args:
        symbol: IDX ticker to filter to, e.g. "BBCA". Omit for general market news.
    """
    return repositories.get_news(get_cache(), get_db(), get_client(), symbol)


@tool
def get_foreign_flow(trade_date: str | None = None) -> dict:
    """Foreign investor net buy/sell flow, market-wide or for a given date.

    Args:
        trade_date: ISO date (YYYY-MM-DD). Omit for whatever the API defaults to —
            unconfirmed, verify the returned date before treating it as "today".
    """
    return repositories.get_foreign_flow(get_cache(), get_db(), get_client(), trade_date)


@tool
def get_broker_activity(symbol: str, trade_date: str) -> dict:
    """Per-broker buy/sell activity for one symbol on one trading date. Past dates
    are cached permanently (immutable once the trading day has closed).

    Args:
        symbol: IDX ticker, e.g. "BBCA".
        trade_date: ISO date (YYYY-MM-DD) of the trading session to look up.
    """
    return repositories.get_broker_activity(get_db(), get_client(), symbol, date.fromisoformat(trade_date))


@tool
def get_corporate_actions_calendar(start: str | None = None, end: str | None = None) -> dict:
    """Market-wide corporate actions calendar (dividends, splits, rights issues,
    etc. across all symbols) for a date window — unlike get_corporate_actions,
    which is scoped to one company.

    Args:
        start: ISO date (YYYY-MM-DD). Omit for whatever the API defaults to.
        end: ISO date (YYYY-MM-DD). Omit for whatever the API defaults to.
    """
    return repositories.get_corporate_actions_calendar(get_cache(), get_db(), get_client(), start, end)


@tool
def get_suspensions() -> dict:
    """Currently/recently suspended IDX symbols, with the reason and source
    document for each suspension."""
    return repositories.get_suspensions(get_cache(), get_db(), get_client())


@tool
def get_top_brokers_daily(trade_date: str | None = None) -> dict:
    """Top brokers ranked by gross trading value for a trading day, with each
    broker's gross/net and foreign gross/net figures.

    Args:
        trade_date: ISO date (YYYY-MM-DD). Omit for whatever the API defaults to
            (observed: the current trading day) — verify the returned `date`
            field before treating it as "today".
    """
    return repositories.get_top_brokers_daily(get_cache(), get_db(), get_client(), trade_date)
