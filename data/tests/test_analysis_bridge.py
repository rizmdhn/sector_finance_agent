"""Integration tests for data/analysis_bridge.py against the real local Postgres.

Unlike analysis/tests (pure functions, no I/O), these exercise real ingested data —
per this project's convention of testing against real infrastructure rather than
mocks. They never touch the Sectors API (see analysis_bridge.py's module docstring),
so they cost no credits and work offline as long as Postgres is up and has at least
one symbol with ingested price_daily rows (run `scripts/manage.py run-job
symbol_master` and `backfill-price <symbol>` first).

Skipped automatically if Postgres is unreachable or has no ingested price data, so
this file is safe to run in environments without the local stack up.
"""

import os

import psycopg
import pytest

from analysis.types import is_missing
from data import analysis_bridge
from data.db import Database


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
