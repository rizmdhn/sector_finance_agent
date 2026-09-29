"""Canonicalization: symbol form, cache key construction, screener query normalization.

Shared by gateway/ and ingest/.
See sectors_idx_ingest_cache_plan_md.md sections 3 and 5, and
data/sectors_client.py's module docstring for what was verified live against the real
API vs. assumed.
"""

import hashlib
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

CACHE_KEY_VERSION = "v1"

IDX_TIMEZONE = ZoneInfo("Asia/Jakarta")


def idx_today() -> date:
    """"Today" for IDX trading-day purposes — Jakarta-local (WIB, UTC+7), not
    whatever timezone the process happens to run in. Matters for real: a
    UTC-clocked container can already be a trading day behind Jakarta — IDX
    closes ~16:00 WIB, and confirmed live a container reading UTC 2026-09-28
    was already 2026-09-29 00:24 in Jakarta, a full trading day apart (see
    PROGRESS.md's backfill-gap fix). Every plain `date.today()` call anywhere
    that means "today, for IDX purposes" should go through this instead."""
    return datetime.now(IDX_TIMEZONE).date()

# Field classification for screener epoch selection. See plan section 3.
# TODO: verify this grouping against the actual field list on the Screener page.
PRICE_DERIVED_FIELD_PREFIXES = (
    "last_close_price",
    "daily_close_change",
    "market_cap",
    "52_week",
    "ytd",
    "tags",
)
QUARTERLY_FIELD_PREFIXES = ("revenue_q", "yoy_quarter", "_mrq", "_ttm")
ANNUAL_FIELD_PREFIXES = ("revenue[", "forecast_")


def canonicalize_symbol(symbol: str) -> str:
    """Uppercase and ensure the ".JK" suffix IDX symbols are keyed under."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    if not symbol.endswith(".JK"):
        symbol = f"{symbol}.JK"
    return symbol


def cache_key(endpoint: str, params: dict, epoch: str | int | None = None) -> str:
    """Build `sec:v1:idx:{endpoint}:{sha1(canonical_params)}[:{epoch}]`.

    See sectors_idx_ingest_cache_plan_md.md section 5.
    """
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha1(canonical_params.encode("utf-8")).hexdigest()
    key = f"sec:{CACHE_KEY_VERSION}:idx:{endpoint}:{digest}"
    if epoch is not None:
        key = f"{key}:{epoch}"
    return key


def resolve_latest_year(today: date | None = None) -> int:
    """Resolve the "latest year" relative screener term to an explicit year.

    The Sectors docs note this shifts meaning between January and April
    (annual reports land through Q1). TODO: verify the exact cutoff.
    """
    today = today or idx_today()
    return today.year - 1 if today.month < 4 else today.year


def _field_class(field: str) -> str:
    lowered = field.lower()
    if any(lowered.startswith(p) or p in lowered for p in PRICE_DERIVED_FIELD_PREFIXES):
        return "price"
    if any(p in lowered for p in QUARTERLY_FIELD_PREFIXES):
        return "quarterly"
    if any(lowered.startswith(p) for p in ANNUAL_FIELD_PREFIXES):
        return "annual"
    return "static"


# Matches an identifier (optionally with bracket notation, e.g. "revenue[2024]")
# immediately followed by a comparison operator, to pull field names out of a
# Sectors SQL-like `where` string such as "sector='Financials' and market_cap>1e12".
_FIELD_TOKEN_RE = re.compile(
    r"([a-zA-Z_][a-zA-Z0-9_]*(?:\[[^\]]*\])?)\s*(?:=|!=|>=|<=|>|<|\blike\b|\bin\b)",
    re.IGNORECASE,
)


def screener_field_classes(where: str, order_by: str | None) -> set[str]:
    """Which epoch/TTL classes a screener query touches, for cache-key epoch selection."""
    fields = _FIELD_TOKEN_RE.findall(where or "")
    if order_by:
        fields.append(order_by.lstrip("-"))
    return {_field_class(f) for f in fields}


def canonicalize_screener_query(where: str | None, order_by: str = "symbol") -> dict:
    """Normalize a screener query into the form used for the cache key and the
    upstream fetch.

    `where` is Sectors' own SQL-like condition string (e.g.
    "sector='Financials' and market_cap>1000000000000") — confirmed live against the
    real API (data/sectors_client.py). It is only whitespace-normalized here, not
    reordered or case-folded: lowercasing or reordering clauses on a live SQL-like
    string risks changing what a quoted string literal matches or breaking operator
    precedence, which the earlier dict-based design didn't have to worry about but
    could silently corrupt query semantics here. This trades away some cache-hit-rate
    optimization from unifying equivalent clauses (e.g. differing only in clause
    order) — a real SQL-like parser/normalizer is a TODO if that turns out to matter.

    `order_by` keeps Sectors' own "-field" convention for descending. `limit`/`offset`
    are deliberately excluded: the fetch always pulls the top-200 rows for this key,
    and the caller's requested `limit`/`offset` are applied by slicing the cached rows
    locally.
    """
    return {
        "where": " ".join((where or "").split()),
        "order_by": order_by.strip(),
    }
