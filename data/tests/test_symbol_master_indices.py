"""Real-Postgres check of symbol_master.indices (skipped if unreachable): the sweep's
upsert stores the list, membership is a parameterized read, NULL means not loaded."""

import os

import psycopg
import pytest

from data.db import Database


def _dsn() -> str:
    return "postgresql://{user}:{password}@{host}:{port}/{db}".format(
        user=os.environ.get("POSTGRES_USER", "idx_agent"),
        password=os.environ.get("POSTGRES_PASSWORD", "devpassword"),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        db=os.environ.get("POSTGRES_DB", "idx_agent"),
    )


def _row(symbol, indices):
    return {"symbol": symbol, "name": symbol, "sector": "S", "subsector": None, "industry": None,
            "subindustry": None, "listing_date": None, "indices": indices}


@pytest.fixture()
def db():
    dsn = _dsn()
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable at " + dsn)
    database = Database(dsn)
    database.init_schema()  # also proves the ALTER ... IF NOT EXISTS is rerunnable
    yield database
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM symbol_master WHERE symbol LIKE 'TST%'")


def test_members_come_from_the_indices_column(db):
    db.upsert_symbol_master([_row("TSTA.JK", ["tstidx", "other"]), _row("TSTB.JK", ["tstidx"]), _row("TSTC.JK", [])])

    assert [m["symbol"] for m in db.get_index_members("tstidx")] == ["TSTA.JK", "TSTB.JK"]
    assert db.get_index_members("no-such-index") == []  # loaded, just empty


def test_resweep_replaces_membership(db):
    db.upsert_symbol_master([_row("TSTD.JK", ["tstgone"])])
    db.upsert_symbol_master([_row("TSTD.JK", [])])  # left the index at the next review

    assert db.get_index_members("tstgone") == []


def test_odd_index_string_is_just_a_value_not_sql(db):
    db.upsert_symbol_master([_row("TSTE.JK", ["lq45"])])  # something loaded, else "not loaded" (None)
    assert db.get_index_members("lq45'] or ['x") == []
