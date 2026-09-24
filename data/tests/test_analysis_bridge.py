"""Integration tests for data/analysis_bridge.py against the real local Postgres.

Unlike analysis/tests (pure functions, no I/O), these exercise real ingested/cached
data — per this project's convention of testing against real infrastructure rather
than mocks. The portfolio/liquidity/returns tests never touch the Sectors API (see
analysis_bridge.py's module docstring); the fundamentals test relies on BBCA's
`overview`/`financials` report sections already being cached in Valkey from prior
manual runs (`scripts/manage.py analyze-fundamentals BBCA`) — it is a pure cache-hit
read, so it costs no credit, but it is skipped if that cache entry is not present
rather than triggering a live (paid) fetch itself.

Skipped automatically if Postgres/Valkey is unreachable or the required data hasn't
been ingested/cached yet, so this file is safe to run in environments without the
local stack up.
"""

import os

import psycopg
import pytest

from analysis.types import is_missing
from data import analysis_bridge
from data.cache import Cache
from data.db import Database
from data.sectors_client import SectorsClient


def _dsn() -> str:
    return "postgresql://{user}:{password}@{host}:{port}/{db}".format(
        user=os.environ.get("POSTGRES_USER", "idx_agent"),
        password=os.environ.get("POSTGRES_PASSWORD", "devpassword"),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        db=os.environ.get("POSTGRES_DB", "idx_agent"),
    )


@pytest.fixture(scope="module")
def db() -> Database:
    dsn = _dsn()
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable at " + dsn)
    return Database(dsn)


@pytest.fixture(scope="module")
def priced_symbol(db: Database) -> str:
    with psycopg.connect(_dsn()) as conn:
        row = conn.execute(
            "SELECT symbol FROM price_daily WHERE close IS NOT NULL GROUP BY symbol "
            "HAVING count(*) >= 5 ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()
    if row is None:
        pytest.skip("no symbol with >=5 ingested price_daily rows; run backfill-price first")
    return row[0]


def test_portfolio_snapshot_uses_real_ingested_price(db: Database, priced_symbol: str):
    result = analysis_bridge.portfolio_snapshot(db, {priced_symbol: 1000.0}, cash=10_000_000)

    assert priced_symbol not in [s for s in result["missing_price_symbols"]]
    assert not is_missing(result["position_values"][priced_symbol])
    assert not is_missing(result["portfolio_value"])
    assert result["portfolio_value"] > 0
    assert 0 < result["weights"][priced_symbol] <= 1
    assert result["hhi"] > 0


def test_portfolio_snapshot_reports_missing_price_without_zeroing():
    # An unknown symbol is rejected before any price lookup (ensure_valid_symbol),
    # exercising the real InvalidSymbolError path against the real symbol_master.
    from data.repositories import InvalidSymbolError

    with pytest.raises(InvalidSymbolError):
        analysis_bridge.portfolio_snapshot(Database(_dsn()), {"NOTREAL": 100}, cash=0)


def test_liquidity_snapshot_real_data(db: Database, priced_symbol: str):
    result = analysis_bridge.liquidity_snapshot(db, priced_symbol, position_value=1_000_000_000)

    assert result["sessions_used"] > 0
    # adv20 needs a full window with no gaps (analysis/liquidity.py's rule) — with
    # fewer than 20 ingested sessions this is correctly UNAVAILABLE, not a fabricated
    # number from a partial window.
    if result["sessions_used"] < result["sessions_requested"]:
        assert is_missing(result["adv20"])
    else:
        assert not is_missing(result["adv20"])


def test_returns_snapshot_real_data(db: Database, priced_symbol: str):
    result = analysis_bridge.returns_snapshot(db, priced_symbol, "1m")

    assert result["sessions_used"] > 0
    assert "price return" in result["label"]
    if len(result["period_returns"]) >= 1:
        assert any(not is_missing(r) for r in result["period_returns"])


@pytest.fixture(scope="module")
def cache() -> Cache:
    try:
        c = Cache(os.environ.get("VALKEY_HOST", "localhost"), int(os.environ.get("VALKEY_PORT", "6379")))
        c._redis.ping()
    except Exception:
        pytest.skip("Valkey not reachable")
    return c


def test_fundamentals_snapshot_matches_sectors_reported_ratios(db: Database, cache: Cache):
    """BBCA's overview+financials sections must already be cached (a prior
    `analyze-fundamentals BBCA` run) — this never fetches live, so it's skipped
    rather than spending a credit if the cache entry is missing.
    """
    key_prefix = "sec:v1:idx:company_report:"
    has_financials_cached = any(
        cache._redis.exists(k) for k in cache._redis.keys(f"{key_prefix}*")
    )
    if not has_financials_cached:
        pytest.skip("BBCA financials not cached yet; run `analyze-fundamentals BBCA` once first")

    client = SectorsClient(api_key="unused-cache-hit-only", base_url="https://api.sectors.app/v2/")
    result = analysis_bridge.fundamentals_snapshot(cache, db, client, "BBCA")

    assert result["is_bank"] is True
    assert result["bank"] is not None
    # Cross-checked against Sectors' own precomputed historical_financial_ratio for
    # BBCA FY2025 — see analysis_bridge.py's _provenance notes for why these fields
    # (net_loan, non_loan_earning_assets alone) were chosen over the more obvious
    # gross_loan / gross_loan+non_loan_earning_assets guesses.
    assert result["bank"]["loan_to_deposit"] == pytest.approx(0.7593527811195531, rel=1e-9)
    assert result["bank"]["nim"] == pytest.approx(0.05669161944896895, rel=1e-9)
    assert result["bank"]["capital_adequacy"] == pytest.approx(0.30367508951660466, rel=1e-9)
    assert not is_missing(result["valuation"]["pe"])
    assert not is_missing(result["valuation"]["pb"])
    # No change-in-operating-NWC field exists in historical_financials at all, so
    # FCFF/FCFE must be UNAVAILABLE rather than approximated from something else.
    assert is_missing(result["valuation"]["fcff"])
    assert is_missing(result["valuation"]["fcfe"])
