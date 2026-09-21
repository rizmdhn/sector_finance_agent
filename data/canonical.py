"""Canonicalization: symbol form, cache key construction, screener query normalization.

Shared by gateway/ and ingest/.
See sectors_idx_ingest_cache_plan_md.md sections 3 and 5.
"""

import hashlib
import json
from datetime import date

CACHE_KEY_VERSION = "v1"

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
    today = today or date.today()
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


def screener_field_classes(where: dict, order_by: str | None) -> set[str]:
    """Which epoch/TTL classes a screener query touches, for cache-key epoch selection."""
    fields = list(where.keys())
    if order_by:
        fields.append(order_by)
    return {_field_class(f) for f in fields}


def _sort_and_clause(conditions: list) -> list:
    """Sort conditions within an AND clause only; never reorder across OR.

    Each condition is either a leaf dict {"field", "op", "value"} or a nested
    {"and": [...]} / {"or": [...]} clause.
    """
    normalized = [_canonicalize_clause(c) for c in conditions]
    return sorted(normalized, key=lambda c: json.dumps(c, sort_keys=True))


def _canonicalize_clause(clause: dict) -> dict:
    if "and" in clause:
        return {"and": _sort_and_clause(clause["and"])}
    if "or" in clause:
        return {"or": [_canonicalize_clause(c) for c in clause["or"]]}
    return {
        "field": str(clause["field"]).lower(),
        "op": str(clause["op"]).lower(),
        "value": clause["value"],
    }


def canonicalize_screener_query(
    where: dict | list,
    order_by: str = "symbol",
    desc: bool = False,
) -> dict:
    """Normalize a screener query into the form used for the cache key and the
    upstream fetch. `limit`/`offset` are deliberately excluded: the fetch always
    pulls the top-200 rows for this key, and the caller's requested `limit` and
    `offset` are applied by slicing the cached rows locally.

    See sectors_idx_ingest_cache_plan_md.md section 3, steps 1-6.
    """
    if isinstance(where, dict):
        where_clause = _canonicalize_clause({"and": [
            {"field": k, "op": "eq", "value": v} for k, v in where.items()
        ]})
    else:
        where_clause = _canonicalize_clause({"and": where})

    return {
        "where": where_clause,
        "order_by": order_by.lower(),
        "desc": desc,
        "include_query_values": True,
    }
