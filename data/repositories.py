"""Repository functions: wire Cache + Database + SectorsClient together per the
strategy decided for each endpoint in sectors_idx_ingest_cache_plan_md.md section 1-2.

gateway/tools/*.py call these, not SectorsClient directly, so the CACHE / INGEST /
LAZY-ATOMIC decision lives in one place per data kind.
"""

from datetime import date, timedelta

from data.cache import EPOCH_FUND, EPOCH_PRICE, Cache
from data.canonical import (
    cache_key,
    canonicalize_screener_query,
    canonicalize_symbol,
    screener_field_classes,
)
from data.db import Database
from data.sectors_client import SectorsClient

# Confirmed live (2026-09-24) against company/report/{symbol}/: the real section
# list is overview, valuation, future, peers, financials, dividend, management,
# ownership. overview/valuation/peers are price-driven (market cap, close price,
# peer multiples); financials/dividend/management/ownership change on a filing/
# reporting cadence, so they key off the symbol version instead of price_epoch.
PRICE_LINKED_REPORT_SECTIONS = {"overview", "valuation", "peers"}

ANNUAL_FIELD_TTL_SECONDS = 24 * 60 * 60
STATIC_FIELD_TTL_SECONDS = 7 * 24 * 60 * 60

PERIOD_TO_DAYS = {"1m": 30, "3m": 90, "1y": 365}


class InvalidSymbolError(ValueError):
    pass


def ensure_valid_symbol(db: Database, symbol: str) -> str:
    """Canonicalize and validate against the ingested symbol master.

    Raises before any API call is made, so a typo costs zero credits.
    """
    canonical = canonicalize_symbol(symbol)
    if not db.is_valid_symbol(canonical):
        raise InvalidSymbolError(f"unknown symbol: {symbol}")
    return canonical


# -- Company Report: CACHE, keyed per (symbol, section) ----------------------


def get_company_report(
    cache: Cache, db: Database, client: SectorsClient, symbol: str, sections: list[str]
) -> dict:
    symbol = ensure_valid_symbol(db, symbol)

    section_keys: dict[str, str] = {}
    result: dict = {}
    missing: list[str] = []

    for section in sections:
        epoch = (
            cache.get_epoch(EPOCH_PRICE)
            if section in PRICE_LINKED_REPORT_SECTIONS
            else cache.get_symbol_version(symbol)
        )
        key = cache_key("company_report", {"symbol": symbol, "section": section}, epoch=epoch)
        section_keys[section] = key
        cached = cache.get(key)
        if cached is None:
            missing.append(section)
        else:
            result[section] = cached

    if not missing:
        return result

    with cache.acquire_lock(f"company_report:{symbol}"):
        still_missing = [s for s in missing if cache.get(section_keys[s]) is None]
        if still_missing:
            fetched = client.get_company_report(symbol, still_missing)
            for section in still_missing:
                value = fetched.get(section)
                cache.set(section_keys[section], value)
                result[section] = value
        for section in missing:
            if section not in result:
                result[section] = cache.get(section_keys[section])

    return result


# -- Screener: CACHE, canonical query -> top-200 rows -------------------------


def screen_companies(
    cache: Cache,
    client: SectorsClient,
    where: str,
    order_by: str = "symbol",
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """`where` is Sectors' own SQL-like condition string, e.g. "sector='Financials'
    and market_cap>1000000000000". Prefix `order_by` with "-" for descending
    (Sectors' own convention, confirmed live — see data/sectors_client.py)."""
    canonical = canonicalize_screener_query(where, order_by)
    classes = screener_field_classes(canonical["where"], canonical["order_by"])

    epoch = None
    ttl = None
    if "price" in classes:
        epoch = cache.get_epoch(EPOCH_PRICE)
    elif "quarterly" in classes:
        epoch = cache.get_epoch(EPOCH_FUND)
    elif "annual" in classes:
        ttl = ANNUAL_FIELD_TTL_SECONDS
    else:
        ttl = STATIC_FIELD_TTL_SECONDS

    key = cache_key("screener", canonical, epoch=epoch)
    rows = cache.get(key)

    if rows is None:
        with cache.acquire_lock(key):
            rows = cache.get(key)
            if rows is None:
                response = client.get_screener(
                    where=canonical["where"] or None, order_by=canonical["order_by"],
                    limit=200, offset=0,
                )
                rows = response.get("results", [])
                cache.set(key, rows, ttl=ttl)

    return rows[offset : offset + limit]


# -- Price history: INGEST-served, read straight from the price store --------


def get_price_history(db: Database, symbol: str, period: str) -> list[dict]:
    symbol = ensure_valid_symbol(db, symbol)
    days = PERIOD_TO_DAYS.get(period)
    if days is None:
        raise ValueError(f"unknown period: {period}")
    end = db.latest_trade_date() or date.today()
    start = end - timedelta(days=days)
    return db.read_price_range(symbol, start, end)


# -- Price detail backfill: LAZY-ATOMIC, fills what the bulk close feed lacks ----


def ensure_price_detail(db: Database, client: SectorsClient, symbol: str) -> None:
    """The bulk daily close feed (ingest/jobs/universe_close.py) only has
    symbol/date/close — confirmed live against the real API, no volume or market cap.
    Liquidity calculations (analysis/liquidity.py) need volume-derived traded value,
    so call this before those to backfill up to 90 days of OHLCV + market cap for one
    symbol via the per-symbol endpoint. Safe to call repeatedly — upsert_price_rows
    overwrites with the latest values without duplicating rows.
    """
    symbol = ensure_valid_symbol(db, symbol)
    rows = client.get_daily_transaction(symbol)
    db.upsert_price_rows(
        [
            {
                "symbol": r["symbol"],
                "trade_date": date.fromisoformat(r["date"]),
                "open": r.get("open"),
                "high": r.get("high"),
                "low": r.get("low"),
                "close": r.get("close"),
                "volume": r.get("volume"),
                "market_cap": r.get("market_cap"),
            }
            for r in rows
        ]
    )


# -- Broker activity per symbol: LAZY-ATOMIC, past days never expire ---------


def get_broker_activity(
    db: Database, client: SectorsClient, symbol: str, trade_date: date
) -> dict:
    symbol = ensure_valid_symbol(db, symbol)
    cached = db.get_broker_activity(symbol, trade_date)
    if cached is not None:
        return cached

    iso_date = trade_date.isoformat()
    rows = client.get_broker_activity_symbol(symbol, start=iso_date, end=iso_date)
    db.store_broker_activity(symbol, trade_date, rows)
    return rows
