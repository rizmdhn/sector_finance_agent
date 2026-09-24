"""Integration tests for data/memory_store.py + data/db.py's user_memory functions
against the real local Postgres — no mocks, matching this project's convention.

Skipped automatically if Postgres is unreachable.
"""

import asyncio
import os
import uuid

import psycopg
import pytest

from data.db import Database
from data.memory_store import PostgresUserMemoryStore, _classify


def _run(coro):
    """No pytest-asyncio/anyio plugin is installed — plain asyncio.run() is enough
    for these single-await calls, avoiding a new dev dependency for one test file.
    """
    return asyncio.run(coro)


def _dsn() -> str:
    return "postgresql://{user}:{password}@{host}:{port}/{db}".format(
        user=os.environ.get("POSTGRES_USER", "idx_agent"),
        password=os.environ.get("POSTGRES_PASSWORD", "devpassword"),
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        db=os.environ.get("POSTGRES_DB", "idx_agent"),
    )


@pytest.fixture()
def db() -> Database:
    dsn = _dsn()
    try:
        with psycopg.connect(dsn, connect_timeout=2):
            pass
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable at " + dsn)
    return Database(dsn)


@pytest.fixture()
def user_id() -> str:
    # A fresh user per test run avoids cross-test/cross-run pollution in a shared
    # real database, rather than relying on cleanup.
    return f"test-user-{uuid.uuid4().hex[:8]}"


def test_add_and_exact_word_search(db: Database, user_id: str):
    store = PostgresUserMemoryStore(db, user_id=user_id)
    _run(store.add("User holds 1000 shares of BBCA", {"kind": "portfolio"}))
    _run(store.add("User's personal concentration limit is 15% per name", {"kind": "mandate"}))

    results = _run(store.search("BBCA"))
    assert any("BBCA" in r.content for r in results)


def test_search_matches_natural_language_query_word_order_differs(db: Database, user_id: str):
    """A live run against the real gateway found the model calls search_memory
    with a descriptive multi-word phrase, not an exact keyword — e.g. "BBCA
    holdings shares position" against stored text "User holds 1000 shares of
    BBCA". Neither a literal substring match nor plainto_tsquery's implicit AND
    (which requires every word, including "position", present) matched this; the
    OR-of-words full-text search in data/db.py::search_user_memory does.
    """
    store = PostgresUserMemoryStore(db, user_id=user_id)
    _run(store.add("User holds 1000 shares of BBCA", {"kind": "portfolio"}))
    _run(store.add("User's personal concentration limit is 15% per name", {"kind": "mandate"}))

    results = _run(store.search("BBCA holdings shares position"))
    assert any("BBCA" in r.content for r in results)

    results = _run(store.search("concentration limit mandate limits"))
    assert any("concentration limit" in r.content for r in results)


def test_search_scoped_to_user(db: Database):
    user_a, user_b = f"test-a-{uuid.uuid4().hex[:8]}", f"test-b-{uuid.uuid4().hex[:8]}"
    store_a = PostgresUserMemoryStore(db, user_id=user_a)
    store_b = PostgresUserMemoryStore(db, user_id=user_b)
    _run(store_a.add("User A holds BBCA"))

    assert any("BBCA" in r.content for r in _run(store_a.search("BBCA")))
    assert _run(store_b.search("BBCA")) == []


def test_search_no_genuine_match_returns_empty_not_unrelated_facts(db: Database, user_id: str):
    store = PostgresUserMemoryStore(db, user_id=user_id)
    _run(store.add("User holds 1000 shares of BBCA"))

    assert _run(store.search("zzzznonexistentwordzzzz")) == []


def test_classify_predefined_kinds():
    assert _classify("User holds 1000 shares of BBCA") == "portfolio"
    assert _classify("User's personal concentration limit is 15% per name") == "mandate_limit"
    assert _classify("User's thesis on BBCA: expect margin expansion from digital lending") == "thesis"
    assert _classify("User prefers answers without jargon") == "preference"
    assert _classify("User's favorite color is blue") == "other"


def test_add_auto_tags_kind_in_metadata(db: Database, user_id: str):
    store = PostgresUserMemoryStore(db, user_id=user_id)
    _run(store.add("User holds 1000 shares of BBCA"))

    results = _run(store.search("BBCA"))
    assert any(r.metadata.get("kind") == "portfolio" for r in results)


def test_add_caller_metadata_overrides_auto_classified_kind(db: Database, user_id: str):
    store = PostgresUserMemoryStore(db, user_id=user_id)
    _run(store.add("User holds 1000 shares of BBCA", {"kind": "custom"}))

    results = _run(store.search("BBCA"))
    assert any(r.metadata.get("kind") == "custom" for r in results)
