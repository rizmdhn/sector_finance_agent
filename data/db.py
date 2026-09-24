"""Postgres access: ingested and lazy-atomic market data store.

Table strategy: see schema.sql and sectors_idx_ingest_cache_plan_md.md section 2.
CACHE-strategy endpoints (screener, company report, ...) are not stored here;
they live only in Valkey (data/cache.py).
"""

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
