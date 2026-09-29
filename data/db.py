"""Postgres access: ingested and lazy-atomic market data store.

Table strategy: see schema.sql and sectors_idx_ingest_cache_plan_md.md section 2.
CACHE-strategy endpoints (screener, company report, ...) are not stored here;
they live only in Valkey (data/cache.py).
"""

import re
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _connect(self):
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def _executemany(self, conn, sql: str, params: list) -> None:
        # psycopg's Connection has no executemany of its own — only Cursor does.
        with conn.cursor() as cur:
            cur.executemany(sql, params)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(SCHEMA_PATH.read_text())

    # -- Reference lists (REFERENCE) -----------------------------------------

    def upsert_reference_list(self, name: str, data: list | dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reference_lists (name, data, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data, updated_at = now()
                """,
                (name, psycopg.types.json.Json(data)),
            )

    def get_reference_list(self, name: str) -> list | dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM reference_lists WHERE name = %s", (name,)
            ).fetchone()
            return row["data"] if row else None

    # -- Symbol master (INGEST) ----------------------------------------------

    def upsert_symbol_master(self, rows: list[dict]) -> None:
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO symbol_master
                    (symbol, name, sector, subsector, industry, subindustry, listing_date, updated_at)
                VALUES (%(symbol)s, %(name)s, %(sector)s, %(subsector)s, %(industry)s,
                        %(subindustry)s, %(listing_date)s, now())
                ON CONFLICT (symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector,
                    subsector = EXCLUDED.subsector,
                    industry = EXCLUDED.industry,
                    subindustry = EXCLUDED.subindustry,
                    listing_date = EXCLUDED.listing_date,
                    updated_at = now()
                """,
                rows,
            )

    def get_symbol_master(self) -> list[dict]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM symbol_master").fetchall()

    def is_valid_symbol(self, symbol: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM symbol_master WHERE symbol = %s", (symbol,)
            ).fetchone()
            return row is not None

    # -- Price store (INGEST) -------------------------------------------------

    def upsert_price_rows(self, rows: list[dict]) -> None:
        """Each row: symbol, trade_date, open, high, low, close, volume, market_cap.

        `COALESCE(EXCLUDED.x, price_daily.x)` on every conflict: the bulk daily-close
        ingest (ingest/jobs/universe_close.py) only ever has `close`, and would
        otherwise silently null out open/high/low/volume/market_cap already filled in
        by the per-symbol LAZY-ATOMIC backfill (data/repositories.py::
        ensure_price_detail), regardless of which one runs second.
        """
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO price_daily
                    (symbol, trade_date, open, high, low, close, volume, market_cap)
                VALUES (%(symbol)s, %(trade_date)s, %(open)s, %(high)s, %(low)s,
                        %(close)s, %(volume)s, %(market_cap)s)
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    open = COALESCE(EXCLUDED.open, price_daily.open),
                    high = COALESCE(EXCLUDED.high, price_daily.high),
                    low = COALESCE(EXCLUDED.low, price_daily.low),
                    close = COALESCE(EXCLUDED.close, price_daily.close),
                    volume = COALESCE(EXCLUDED.volume, price_daily.volume),
                    market_cap = COALESCE(EXCLUDED.market_cap, price_daily.market_cap)
                """,
                rows,
            )

    def read_price_range(self, symbol: str, start: date, end: date) -> list[dict]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM price_daily
                WHERE symbol = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
                """,
                (symbol, start, end),
            ).fetchall()

    def latest_trade_date(self) -> date | None:
        with self._connect() as conn:
            row = conn.execute("SELECT max(trade_date) AS d FROM price_daily").fetchone()
            return row["d"] if row else None

    def get_latest_close(self, symbol: str) -> tuple[date, float] | None:
        """Most recent trade_date with a non-null close for one symbol, for
        portfolio valuation (analysis/portfolio.py via data/analysis_bridge.py).
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT trade_date, close FROM price_daily
                WHERE symbol = %s AND close IS NOT NULL
                ORDER BY trade_date DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            return (row["trade_date"], row["close"]) if row else None

    # -- Quarterly-dates change detector (INGEST) -----------------------------

    def diff_quarterly_dates(self, latest: dict[str, date]) -> list[str]:
        """Compare `latest` (symbol -> report date) against the stored snapshot,
        update the snapshot, and return the symbols whose date changed.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, latest_report_date FROM quarterly_dates_snapshot"
            ).fetchall()
            previous = {r["symbol"]: r["latest_report_date"] for r in rows}

            changed = [
                symbol
                for symbol, report_date in latest.items()
                if previous.get(symbol) != report_date
            ]

            self._executemany(
                conn,
                """
                INSERT INTO quarterly_dates_snapshot (symbol, latest_report_date, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (symbol) DO UPDATE SET
                    latest_report_date = EXCLUDED.latest_report_date,
                    updated_at = now()
                """,
                [(symbol, latest[symbol]) for symbol in changed],
            )
            return changed

    # -- Corporate actions calendar (INGEST) ----------------------------------

    def upsert_corporate_actions(self, rows: list[dict]) -> None:
        """Each row: symbol, action_type, ex_date, payload."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO corporate_actions (symbol, action_type, ex_date, payload, updated_at)
                VALUES (%(symbol)s, %(action_type)s, %(ex_date)s, %(payload)s, now())
                ON CONFLICT (symbol, action_type, ex_date) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                [
                    {**r, "payload": psycopg.types.json.Json(r["payload"])}
                    for r in rows
                ],
            )

    def read_corporate_actions(self, symbol: str) -> list[dict]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM corporate_actions WHERE symbol = %s ORDER BY ex_date",
                (symbol,),
            ).fetchall()

    # -- Foreign flow, broker rankings, suspensions, filings, news (INGEST) --

    def upsert_foreign_flow(self, rows: list[dict]) -> None:
        """Each row: symbol, trade_date, net_value."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO foreign_flow_daily (symbol, trade_date, net_value)
                VALUES (%(symbol)s, %(trade_date)s, %(net_value)s)
                ON CONFLICT (symbol, trade_date) DO UPDATE SET net_value = EXCLUDED.net_value
                """,
                rows,
            )

    def read_foreign_flow(self, symbol: str, start: date, end: date) -> list[dict]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM foreign_flow_daily
                WHERE symbol = %s AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date
                """,
                (symbol, start, end),
            ).fetchall()

    def upsert_broker_rankings(self, trade_date_: date, rows: list[dict]) -> None:
        """Each row: broker_code, payload (all brokers, unfiltered)."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO broker_rankings (trade_date, broker_code, payload)
                VALUES (%(trade_date)s, %(broker_code)s, %(payload)s)
                ON CONFLICT (trade_date, broker_code) DO UPDATE SET payload = EXCLUDED.payload
                """,
                [
                    {
                        "trade_date": trade_date_,
                        "broker_code": r["broker_code"],
                        "payload": psycopg.types.json.Json(r["payload"]),
                    }
                    for r in rows
                ],
            )

    def upsert_suspensions(self, rows: list[dict]) -> None:
        """Each row: symbol, suspended_at, reason."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO suspensions (symbol, suspended_at, reason)
                VALUES (%(symbol)s, %(suspended_at)s, %(reason)s)
                ON CONFLICT (symbol, suspended_at) DO UPDATE SET reason = EXCLUDED.reason
                """,
                rows,
            )

    def upsert_filings(self, rows: list[dict]) -> None:
        """Each row: symbol, holder_type, filed_at, payload."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO filings (symbol, holder_type, filed_at, payload)
                VALUES (%(symbol)s, %(holder_type)s, %(filed_at)s, %(payload)s)
                """,
                [
                    {**r, "payload": psycopg.types.json.Json(r["payload"])}
                    for r in rows
                ],
            )

    def upsert_news_articles(self, rows: list[dict]) -> None:
        """Each row: symbol, extension, published_at, title, url, tags, body."""
        with self._connect() as conn:
            self._executemany(
                conn,
                """
                INSERT INTO news_articles
                    (symbol, extension, published_at, title, url, tags, body)
                VALUES (%(symbol)s, %(extension)s, %(published_at)s, %(title)s,
                        %(url)s, %(tags)s, %(body)s)
                """,
                [
                    {**r, "tags": psycopg.types.json.Json(r.get("tags"))}
                    for r in rows
                ],
            )

    # -- Broker activity (LAZY-ATOMIC) ----------------------------------------

    def get_broker_activity(self, symbol: str, trade_date_: date) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM broker_activity WHERE symbol = %s AND trade_date = %s",
                (symbol, trade_date_),
            ).fetchone()
            return row["payload"] if row else None

    def store_broker_activity(self, symbol: str, trade_date_: date, payload: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO broker_activity (symbol, trade_date, payload, fetched_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    payload = EXCLUDED.payload, fetched_at = now()
                """,
                (symbol, trade_date_, psycopg.types.json.Json(payload)),
            )

    # -- User memory, long-term (data/memory_store.py::PostgresUserMemoryStore) ----

    def add_user_memory(self, user_id: str, content: str, metadata: dict | None) -> dict:
        with self._connect() as conn:
            return conn.execute(
                """
                INSERT INTO user_memory (user_id, content, metadata) VALUES (%s, %s, %s)
                RETURNING id, content, metadata, created_at
                """,
                (user_id, content, psycopg.types.json.Json(metadata) if metadata is not None else None),
            ).fetchone()

    def search_user_memory(self, user_id: str, query: str | None, limit: int) -> list[dict]:
        """Postgres full-text search (`to_tsvector`/`plainto_tsquery`) on `content`,
        ranked by relevance — zero-cost (no embeddings/model call), matching this
        project's credit-consciousness. A live run showed a plain `ILIKE
        '%<whole query>%'` essentially never matches: the model calls this with a
        natural-language phrase like "concentration limit mandate limits" against
        stored text like "User's personal concentration limit is 15% per name" —
        different word order, so a single-substring match fails even though every
        word is present.

        Full-text search alone wasn't enough either, confirmed live: `plainto_tsquery`
        ANDs every word in the query together, so a 4-word query like "BBCA holdings
        shares position" needs all 4 lexemes present in the stored text — "position"
        alone being absent zeroed out an otherwise-good match. OR-ing the query's
        words instead (built here, not via plainto_tsquery) is the right fit for a
        loose "find anything related" recall tool, ranked by how many terms matched
        so a fact matching more of the query still sorts first.

        Genuinely no match returns empty, not a substitute of unrelated recent
        facts — presenting those as if they matched would misrepresent them as
        relevant, the same discipline this project applies to any other missing
        result. Pass `query=None` (not an empty-match query) to explicitly list the
        most recent facts unfiltered.
        """
        with self._connect() as conn:
            if query:
                # Alphanumeric words only, so this can never be interpreted as
                # tsquery operator syntax (&, |, !, (, ), :*) — safe to join with " | ".
                words = re.findall(r"[A-Za-z0-9]+", query)
                if not words:
                    return []
                or_query = " | ".join(words)
                return conn.execute(
                    """
                    SELECT id, content, metadata, created_at FROM user_memory
                    WHERE user_id = %s AND status = 'active'
                      AND to_tsvector('english', content) @@ to_tsquery('english', %s)
                    ORDER BY ts_rank(to_tsvector('english', content), to_tsquery('english', %s)) DESC
                    LIMIT %s
                    """,
                    (user_id, or_query, or_query, limit),
                ).fetchall()
            return conn.execute(
                """
                SELECT id, content, metadata, created_at FROM user_memory
                WHERE user_id = %s AND status = 'active'
                ORDER BY created_at DESC LIMIT %s
                """,
                (user_id, limit),
            ).fetchall()

    def invalidate_user_memory(self, user_id: str, memory_id: int) -> None:
        """Consolidation's UPDATE action: retire a fact a newer one supersedes,
        without deleting it (keeps the audit trail — data/schema.sql's comment)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE user_memory SET status = 'invalid' WHERE id = %s AND user_id = %s",
                (memory_id, user_id),
            )

    def get_summary_for_session(self, user_id: str, session_id: str) -> dict | None:
        """The one active `kind: summary` row tagged with this session_id, if any —
        data/memory_store.py::write_summary upserts against this instead of
        consolidating summaries the way write_memory consolidates facts."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT id, content, metadata, created_at FROM user_memory
                WHERE user_id = %s AND status = 'active' AND metadata->>'kind' = 'summary'
                  AND metadata->>'session_id' = %s
                """,
                (user_id, session_id),
            ).fetchone()

    def get_memory_settings(self, user_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT auto_extraction FROM user_memory_settings WHERE user_id = %s", (user_id,)
            ).fetchone()
            return {"auto_extraction": bool(row["auto_extraction"]) if row else False}

    def set_memory_settings(self, user_id: str, auto_extraction: bool) -> dict:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_memory_settings (user_id, auto_extraction) VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET auto_extraction = EXCLUDED.auto_extraction, updated_at = now()
                """,
                (user_id, auto_extraction),
            )
            return {"auto_extraction": auto_extraction}

    def delete_user_memory(self, user_id: str, memory_id: int) -> bool:
        """Scoped to `user_id` as well as `id` so one user can never delete
        another's memory row by guessing/iterating ids."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_memory WHERE id = %s AND user_id = %s",
                (memory_id, user_id),
            )
            return cur.rowcount > 0

    def update_user_memory(self, user_id: str, memory_id: int, content: str) -> dict | None:
        """Same `(id, user_id)` scoping as delete_user_memory. Content only, not
        metadata — `created_at` stays the original write time (this edits a fact,
        it doesn't re-date it); `metadata`'s `kind` tag stays whatever it was, an
        edit doesn't reclassify it. Returns None if no row matched, same shape as
        delete_user_memory's bool return, so the caller can 404 the same way."""
        with self._connect() as conn:
            return conn.execute(
                """
                UPDATE user_memory SET content = %s WHERE id = %s AND user_id = %s
                RETURNING id, content, metadata, created_at
                """,
                (content, memory_id, user_id),
            ).fetchone()

    # -- Per-user model tier overrides (gateway/roles/orchestrator.py) ----------

    def get_role_tiers(self, user_id: str) -> dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role_id, tier FROM role_tier_config WHERE user_id = %s", (user_id,)
            ).fetchall()
            return {row["role_id"]: row["tier"] for row in rows}

    def set_role_tier(self, user_id: str, role_id: str, tier: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO role_tier_config (user_id, role_id, tier) VALUES (%s, %s, %s)
                ON CONFLICT (user_id, role_id) DO UPDATE SET tier = EXCLUDED.tier, updated_at = now()
                """,
                (user_id, role_id, tier),
            )

    # -- Eval runs (gateway/eval_runner.py) --------------------------------------

    def create_eval_run(self, user_id: str, judge_provider: str, judge_model: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO eval_runs (user_id, judge_provider, judge_model) VALUES (%s, %s, %s)
                RETURNING id
                """,
                (user_id, judge_provider, judge_model),
            ).fetchone()
            return row["id"]

    def start_eval_run(self, run_id: int, total: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'running', total = %s, updated_at = now() WHERE id = %s",
                (total, run_id),
            )

    def progress_eval_run(self, run_id: int, completed: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE eval_runs SET completed = %s, updated_at = now() WHERE id = %s", (completed, run_id)
            )

    def finish_eval_run(self, run_id: int, summary: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'done', summary = %s, updated_at = now() WHERE id = %s",
                (psycopg.types.json.Json(summary), run_id),
            )

    def fail_eval_run(self, run_id: int, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'error', error = %s, updated_at = now() WHERE id = %s",
                (error, run_id),
            )

    def cancel_eval_run(self, run_id: int) -> None:
        """eval_runner.py calls this once it actually stops (after finishing
        whichever conversation was in flight), so `status` only ever flips to
        'cancelled' when the run has really exited, not just been asked to."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE eval_runs SET status = 'cancelled', updated_at = now() WHERE id = %s", (run_id,)
            )

    def request_eval_run_cancel(self, run_id: int) -> bool:
        """Only takes effect on a still-in-flight run — a finished run has
        nothing left to stop. Returns whether it applied."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE eval_runs SET cancel_requested = true WHERE id = %s AND status IN ('pending', 'running')",
                (run_id,),
            )
            return cur.rowcount > 0

    def is_eval_run_cancelled(self, run_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT cancel_requested FROM eval_runs WHERE id = %s", (run_id,)).fetchone()
            return bool(row and row["cancel_requested"])

    def get_eval_run(self, run_id: int) -> dict | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM eval_runs WHERE id = %s", (run_id,)).fetchone()

    def list_eval_runs(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
