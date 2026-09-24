-- Ingested and lazy-atomic market data store.
-- See sectors_idx_ingest_cache_plan_md.md section 2 for the strategy behind each table.
-- CACHE-strategy data (screener, company report, subsector report, ...) lives in
-- Valkey only and has no table here.

-- REFERENCE: subsectors, industries, subindustries, news tags, broker registry,
-- companies-with-revenue-segments. One row per list, refreshed weekly wholesale.
CREATE TABLE IF NOT EXISTS reference_lists (
    name        TEXT PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- INGEST: symbol master, built from a weekly full sweep of the screener.
CREATE TABLE IF NOT EXISTS symbol_master (
    symbol       TEXT PRIMARY KEY,   -- canonical form, e.g. BBCA.JK
    name         TEXT,
    sector       TEXT,
    subsector    TEXT,
    industry     TEXT,
    subindustry  TEXT,
    listing_date DATE,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- INGEST: daily full-universe close. Primary price store.
CREATE TABLE IF NOT EXISTS price_daily (
    symbol      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      BIGINT,
    market_cap  NUMERIC,
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS price_daily_trade_date_idx ON price_daily (trade_date);

-- INGEST change detector: latest quarterly financial dates, per symbol.
-- Diffing the previous snapshot against a new pull bumps the symbol's
-- Valkey version (Cache.bump_symbol_version) and fund_epoch.
CREATE TABLE IF NOT EXISTS quarterly_dates_snapshot (
    symbol              TEXT PRIMARY KEY,
    latest_report_date  DATE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- INGEST: corporate actions calendar. Per-symbol views are served from this.
CREATE TABLE IF NOT EXISTS corporate_actions (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL,
    action_type TEXT NOT NULL,
    ex_date     DATE,
    payload     JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, action_type, ex_date)
);
CREATE INDEX IF NOT EXISTS corporate_actions_symbol_idx ON corporate_actions (symbol);

-- INGEST: company filings (insider).
CREATE TABLE IF NOT EXISTS filings (
    id          BIGSERIAL PRIMARY KEY,
    symbol      TEXT NOT NULL,
    holder_type TEXT,
    filed_at    TIMESTAMPTZ NOT NULL,
    payload     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS filings_symbol_filed_at_idx ON filings (symbol, filed_at);

-- INGEST: news articles (IDX + mining extension).
CREATE TABLE IF NOT EXISTS news_articles (
    id           BIGSERIAL PRIMARY KEY,
    symbol       TEXT,
    extension    TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    title        TEXT,
    url          TEXT,
    tags         JSONB,
    body         TEXT
);
CREATE INDEX IF NOT EXISTS news_articles_symbol_idx ON news_articles (symbol);
CREATE INDEX IF NOT EXISTS news_articles_published_at_idx ON news_articles (published_at);

-- INGEST: stock suspensions.
CREATE TABLE IF NOT EXISTS suspensions (
    symbol        TEXT NOT NULL,
    suspended_at  DATE NOT NULL,
    reason        TEXT,
    PRIMARY KEY (symbol, suspended_at)
);

-- INGEST: top brokers daily ranking, unfiltered. Filter by origin/cohort locally.
CREATE TABLE IF NOT EXISTS broker_rankings (
    trade_date  DATE NOT NULL,
    broker_code TEXT NOT NULL,
    payload     JSONB NOT NULL,
    PRIMARY KEY (trade_date, broker_code)
);

-- INGEST: daily full-universe foreign flow.
CREATE TABLE IF NOT EXISTS foreign_flow_daily (
    symbol      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    net_value   NUMERIC,
    PRIMARY KEY (symbol, trade_date)
);

-- LAZY-ATOMIC: broker activity per symbol/date. No universe feed, so only
-- watchlist/hot symbols get ingested; past days never expire once fetched.
CREATE TABLE IF NOT EXISTS broker_activity (
    symbol      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);

-- LONG-TERM per-user memory (durable, unlike Valkey's session store — see
-- data/session_repository.py for the short-term/session counterpart). Free-form
-- by design ("anything user related should be configurable"): a fact is whatever
-- text the user or the agent decided was worth remembering (a portfolio, a mandate
-- limit, a recorded thesis, a preference), not a fixed set of columns. Read/written
-- via data/memory_store.py::PostgresUserMemoryStore, which implements Strands'
-- MemoryStore protocol so the Chief's `remember`/`recall` tools are Strands' own
-- add_memory/search_memory, not hand-rolled ones.
CREATE TABLE IF NOT EXISTS user_memory (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS user_memory_user_id_idx ON user_memory (user_id, created_at DESC);
