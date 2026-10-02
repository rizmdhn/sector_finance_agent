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
    idx_today,
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
COMPANY_REPORT_SECTIONS = (
    "overview", "valuation", "future", "peers", "financials", "dividend", "management", "ownership",
)

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
    # Before any API call: the API 400s on an unknown section (found live — an agent
    # asked for cash_flow/balance_sheet/risk, none of which exist; cash flow and
    # balance sheet lines are inside `financials`). The message names the valid ones
    # so the model's retry can succeed.
    unknown = [name for name in sections if name not in COMPANY_REPORT_SECTIONS]
    if unknown:
        raise ValueError(
            f"unknown company report section(s): {', '.join(unknown)}. "
            f"Valid sections: {', '.join(COMPANY_REPORT_SECTIONS)}. "
            "Cash flow and balance sheet figures are inside `financials`."
        )
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


# -- Shareholders composition: CACHE, keyed per symbol -------------------------
# Confirmed live (2026-09-29) against company/shareholders-composition/{symbol}/:
# a bare dict {"symbol", "year", "data": [...]}, one entry per month, each with
# local/foreign holder-category breakdowns (insurance_l/_f, corporate_l/_f, ...,
# total_l, total_f), numbers_of_shareholders, and change_in_shareholders. This is
# ownership COMPOSITION by holder category and domestic/foreign split — it does
# NOT name individual major shareholders or map holdings to a controlling group;
# that remains portfolio-intelligence-data-gap-analysis-v1.md's G1 (real gap, no
# such endpoint exists). Keyed off the symbol version epoch, same bucket as
# get_company_report's non-price-linked sections (financials/dividend/management/
# ownership) — this data changes on a reporting cadence, not daily price moves.


def get_shareholders_composition(cache: Cache, db: Database, client: SectorsClient, symbol: str) -> dict:
    symbol = ensure_valid_symbol(db, symbol)
    epoch = cache.get_symbol_version(symbol)
    key = cache_key("shareholders_composition", {"symbol": symbol}, epoch=epoch)
    cached = cache.get(key)
    if cached is not None:
        return cached

    with cache.acquire_lock(f"shareholders_composition:{symbol}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_shareholders_composition(symbol)
            cache.set(key, cached)
    return cached


# -- Free float: REFERENCE, whole-market weekly list ---------------------------
# ingest/jobs/symbol_master.py's weekly REFERENCE_LISTS sweep already pulls and
# stores this (reference_lists table, name="free_float") — this is just the
# per-symbol lookup into that already-ingested list. Zero Sectors API credit:
# reads Postgres only, same as the other REFERENCE-strategy lookups.


def get_free_float(db: Database, symbol: str) -> float | None:
    """`free_float` is a fraction of total shares outstanding (e.g. 0.9989 = 99.89%
    free float), confirmed live (2026-09-29) — one row per symbol,
    {"symbol", "company_name", "free_float"}. Linear scan over ~961 rows: cheap,
    refreshed weekly, not worth a dedicated indexed table for this."""
    symbol = ensure_valid_symbol(db, symbol)
    rows = db.get_reference_list("free_float") or []
    for row in rows:
        if row.get("symbol") == symbol:
            return row.get("free_float")
    return None


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


def get_index_constituents(db: Database, index: str) -> list[dict]:
    """Current members of one index ("lq45", "idx30", ...) as [{symbol, name}], read
    from symbol_master.indices — which the weekly ticker sweep fills for every index at
    once, so this never calls Sectors and costs no credit. An unknown index name just
    has no members."""
    members = db.get_index_members(index.strip().lower())
    if members is None:
        raise RuntimeError(
            "index membership hasn't been loaded yet — it arrives with the ticker sweep "
            "(`python -m ingest.cli seed`, or the weekly Monday run)"
        )
    return members


# -- Price history: INGEST-served, read straight from the price store --------


def get_price_history(db: Database, symbol: str, period: str) -> list[dict]:
    symbol = ensure_valid_symbol(db, symbol)
    days = PERIOD_TO_DAYS.get(period)
    if days is None:
        raise ValueError(f"unknown period: {period}")
    end = db.latest_trade_date() or idx_today()
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


# -- Event/news data: CACHE (Valkey, short TTL) + durable audit trail (Postgres) --
#
# Corporate actions, filings, news, and foreign flow have no change-detector or
# epoch tracking their update cadence (unlike price_epoch/fund_epoch) — there is no
# signal here for "has this actually changed" the way there is for prices or
# financials. A flat TTL is a deliberate compromise: short enough that a market
# question doesn't answer from meaningfully stale data, long enough that the same
# symbol asked about twice in quick succession (a real, observed usage pattern —
# see PROGRESS.md's earlier BBCA-caching discussion) costs 1 credit, not N. If this
# TTL turns out wrong for how fast any of these actually change, tighten or loosen
# it per endpoint rather than assuming one number fits all four.
#
# Valkey alone was the original design here — the user noticed live that a real
# Sectors API call for news never showed up in Postgres, unlike every other real
# data source in this project (price_daily, broker_activity, user_memory). That's a
# real gap for this product: the Independent Risk and Evidence Officer's job is to
# "follow a material claim back to its source" (business doc), and Valkey's 1-hour
# TTL means that source could be gone within the hour a review happens, with no
# durable record of exactly what the agent saw and when. So every REAL fetch (cache
# miss only — a Valkey hit writes nothing new) also gets persisted below, using
# tables that already existed in data/schema.sql from an earlier, unfinished
# INGEST-job design (corporate_actions, filings, news_articles, foreign_flow_daily)
# but had no writer until now. This costs zero extra Sectors credit — it's an extra
# Postgres write on data already paid for, not an extra API call.
EVENT_DATA_TTL_SECONDS = 60 * 60


def _corporate_action_rows(symbol: str, payload: dict) -> list[dict]:
    """Flattens the API's {"corporate_actions": {action_type: [item, ...]}} shape
    into one row per item, matching Database.upsert_corporate_actions. Different
    action types use different date field names (dividend: ex_date, stock_split:
    date, agm: agm_date) — tried in that order; a type/item with none of these
    (e.g. bonus/warrant/right_issue when null in the real BBCA response) just gets
    a NULL ex_date rather than being dropped, since the payload itself is still
    worth keeping as an audit record even without a dedup date.
    """
    by_type = (payload or {}).get("corporate_actions") or {}
    rows = []
    for action_type, items in by_type.items():
        for item in items or []:
            ex_date = item.get("ex_date") or item.get("date") or item.get("agm_date")
            rows.append({"symbol": symbol, "action_type": action_type, "ex_date": ex_date, "payload": item})
    return rows


def _filing_rows(symbol: str | None, payload: dict) -> list[dict]:
    """Matches Database.upsert_filings. `filed_at` is NOT NULL in the schema, so a
    result item with no `timestamp` is skipped rather than inserted with a
    fabricated date — not observed in practice against the real API, but cheap
    insurance against a write-time crash on a future response shape change."""
    return [
        {"symbol": item.get("symbol") or symbol, "holder_type": item.get("holder_type"), "filed_at": item["timestamp"], "payload": item}
        for item in (payload or {}).get("results") or []
        if item.get("timestamp")
    ]


def _news_rows(symbol: str | None, payload: dict) -> list[dict]:
    """Matches Database.upsert_news_articles. One real article can cover multiple
    symbols (`symbols: [...]`, confirmed live) — stored as one row per symbol so a
    later per-symbol query finds it, at the cost of duplicate rows for a
    multi-symbol article. `extension` is NOT NULL in the schema with no equivalent
    field in the real response; set to a constant "idx" rather than left to guess
    at the original ingest plan's unimplemented multi-source distinction."""
    rows = []
    for item in (payload or {}).get("results") or []:
        if not item.get("timestamp"):
            continue
        symbols_covered = item.get("symbols") or ([symbol] if symbol else [None])
        for covered in symbols_covered:
            rows.append(
                {
                    "symbol": covered,
                    "extension": "idx",
                    "published_at": item["timestamp"],
                    "title": item.get("title"),
                    "url": item.get("source"),
                    "tags": item.get("tags"),
                    "body": item.get("body"),
                }
            )
    return rows


def _foreign_flow_rows(payload: dict) -> list[dict]:
    """Matches Database.upsert_foreign_flow. Confirmed live: this is a market-wide
    top-N-by-inflow feed, not filterable to one symbol (data/sectors_client.py) —
    every row here covers whatever symbols the API chose to return that date, not
    necessarily the one the caller asked about."""
    return [
        {"symbol": item["symbol"], "trade_date": item["date"], "net_value": item.get("net_foreign_inflow")}
        for item in (payload or {}).get("results") or []
        if item.get("symbol") and item.get("date")
    ]


def get_corporate_actions(cache: Cache, db: Database, client: SectorsClient, symbol: str) -> dict:
    symbol = ensure_valid_symbol(db, symbol)
    key = cache_key("corporate_actions", {"symbol": symbol})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"corporate_actions:{symbol}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_corporate_actions(symbol)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _corporate_action_rows(symbol, cached)
            if rows:
                db.upsert_corporate_actions(rows)
    return cached


def get_filings(cache: Cache, db: Database, client: SectorsClient, symbol: str | None) -> dict:
    if symbol is not None:
        symbol = ensure_valid_symbol(db, symbol)
    key = cache_key("filings", {"symbol": symbol})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"filings:{symbol or 'all'}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_filings(symbol)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _filing_rows(symbol, cached)
            if rows:
                db.upsert_filings(rows)
    return cached


def get_news(cache: Cache, db: Database, client: SectorsClient, symbol: str | None) -> dict:
    if symbol is not None:
        symbol = ensure_valid_symbol(db, symbol)
    key = cache_key("news", {"symbol": symbol})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"news:{symbol or 'all'}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_news(symbol)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _news_rows(symbol, cached)
            if rows:
                db.upsert_news_articles(rows)
    return cached


def get_foreign_flow(cache: Cache, db: Database, client: SectorsClient, trade_date: str | None) -> dict:
    """`trade_date`: ISO date string, or None for whatever the API defaults to
    (unconfirmed — see data/sectors_client.py's note that filter params on this
    endpoint were never verified to actually filter)."""
    key = cache_key("foreign_flow", {"trade_date": trade_date})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"foreign_flow:{trade_date or 'latest'}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_foreign_flow_daily(trade_date)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _foreign_flow_rows(cached)
            if rows:
                db.upsert_foreign_flow(rows)
    return cached


# -- Corporate actions calendar, suspensions, top brokers: CACHE + durable audit --
#
# These three were confirmed to exist (data/sectors_client.py) but had no
# repository wrapper, no gateway tool, and had never actually been called against
# the real API until now — first live calls (2026-09-24) confirmed each shape below
# directly, not guessed. Same CACHE + Postgres-audit-trail pattern as
# get_corporate_actions/get_filings/get_news/get_foreign_flow above.


def _corporate_action_calendar_rows(payload: dict) -> list[dict]:
    """The bulk calendar's shape differs from the per-symbol endpoint's: no
    wrapping "corporate_actions" key, and each item carries its OWN `symbol`
    (confirmed live: {"start":..., "end":..., "dividend": [{"symbol": "BBCA.JK",
    "ex_date": ..., ...}], ...}). Reuses the `corporate_actions` table/upsert —
    same (symbol, action_type, ex_date) shape as _corporate_action_rows above.
    """
    rows = []
    for action_type, items in (payload or {}).items():
        if action_type in ("start", "end") or not isinstance(items, list):
            continue
        for item in items:
            symbol = item.get("symbol")
            if not symbol:
                continue
            ex_date = item.get("ex_date") or item.get("date") or item.get("agm_date")
            rows.append({"symbol": symbol, "action_type": action_type, "ex_date": ex_date, "payload": item})
    return rows


def _suspension_rows(payload: dict) -> list[dict]:
    """Matches Database.upsert_suspensions (symbol, suspended_at, reason). The
    real response also carries a `pdf_url` per suspension that the existing
    `suspensions` table (data/schema.sql) has no column for — dropped here rather
    than widening the schema for one field; still present in the live tool result
    the agent sees, just not persisted."""
    return [
        {"symbol": item["symbol"], "suspended_at": item.get("suspension_date"), "reason": item.get("reason")}
        for item in (payload or {}).get("results") or []
        if item.get("symbol") and item.get("suspension_date")
    ]


def _top_broker_rows(payload: dict) -> tuple[str | None, list[dict]]:
    """Matches Database.upsert_broker_rankings (trade_date_, rows of
    {broker_code, payload}) — the real response's `date` field names which
    trading day these rankings are for; each result row is stored whole as the
    per-broker payload (rank, gross, net, foreign_gross, foreign_net)."""
    trade_date_str = (payload or {}).get("date")
    rows = [
        {"broker_code": item["broker_code"], "payload": item}
        for item in (payload or {}).get("results") or []
        if item.get("broker_code")
    ]
    return trade_date_str, rows


def get_corporate_actions_calendar(
    cache: Cache, db: Database, client: SectorsClient, start: str | None, end: str | None
) -> dict:
    """`start`/`end`: ISO dates, or None for whatever the API defaults to
    (confirmed live: a 2-month window ending ~1 month out from today when called
    with no params — but that default window is observed behavior, not a
    documented contract, so don't assume it holds forever)."""
    key = cache_key("corporate_actions_calendar", {"start": start, "end": end})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"corporate_actions_calendar:{start}:{end}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_corporate_actions_calendar(start, end)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _corporate_action_calendar_rows(cached)
            if rows:
                db.upsert_corporate_actions(rows)
    return cached


def get_suspensions(cache: Cache, db: Database, client: SectorsClient) -> dict:
    key = cache_key("suspensions", {})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock("suspensions"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_suspensions()
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            rows = _suspension_rows(cached)
            if rows:
                db.upsert_suspensions(rows)
    return cached


def get_top_brokers_daily(cache: Cache, db: Database, client: SectorsClient, trade_date: str | None) -> dict:
    key = cache_key("top_brokers_daily", {"trade_date": trade_date})
    cached = cache.get(key)
    if cached is not None:
        return cached
    with cache.acquire_lock(f"top_brokers_daily:{trade_date or 'latest'}"):
        cached = cache.get(key)
        if cached is None:
            cached = client.get_top_brokers_daily(trade_date)
            cache.set(key, cached, ttl=EVENT_DATA_TTL_SECONDS)
            trade_date_str, rows = _top_broker_rows(cached)
            if rows and trade_date_str:
                db.upsert_broker_rankings(date.fromisoformat(trade_date_str), rows)
    return cached
