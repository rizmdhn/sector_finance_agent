# IDX Agent — Progress Checkpoint

Resume point if this session is lost. Source specs: `idx_agent_infrastructure_diagrams_md.md`
(architecture) and `sectors_idx_ingest_cache_plan_md.md` (per-endpoint cache/ingest strategy)
— the user holds these and will drop them into the repo; they are not committed here yet.
This is now a git repo (`github.com` remote `origin`, branch `main`) with commits — see
`git log` for what's actually committed vs. still-untracked local changes.

**UI decision is open.** The infra doc's original pick was LibreChat, but LibreChat +
MongoDB + Meilisearch have been pulled out of `docker-compose.yml` — user is leaning
towards Open WebUI instead. `librechat.yaml` is kept in case that changes.
`docker-compose.override.yml` (which only existed to mount `librechat.yaml` into a
`librechat` service) was deleted since it referenced a service that no longer exists.
The gateway itself is UI-agnostic (plain OpenAI-compatible API), so this doesn't block
anything else.

## Before running anything

- **Python 3.10+ required.** This codebase uses `X | Y` union type syntax throughout.
  The machine's system `python3` was 3.9 and fails on it with a `TypeError` at import
  time. Everything in this session was tested against Homebrew's `python3.14`
  (`/opt/homebrew/bin/python3.14`) — use that or newer, not system `python3`.
- **No `.env` exists yet, only `.env.example`.** Real values are needed for
  `SECTORS_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `IDX_GATEWAY_KEY`,
  `POSTGRES_*`, `VALKEY_*` before anything beyond unit-level testing (fakeredis,
  mocked agents) is possible.
- **`models.yaml` still has `model_id: TODO` for both entries.** Loading the registry
  works fine (nothing validates the value), but building a real model will fail or
  hit a nonexistent model until real Anthropic/OpenAI model IDs are filled in.

## Done

### 1. Skeleton (repo layout, section 10 of the infra doc)
All directories exist: `gateway/`, `data/`, `ingest/`, `evals/`, plus `docker-compose.yml`,
`librechat.yaml` (unused for now — see UI decision note above), `models.yaml`,
`.env.example`.

### 2. `data/` — fully implemented datasource layer
- `config.py` — `Settings.from_env()`.
- `canonical.py` — symbol canonicalization (`BBCA` → `BBCA.JK`), cache key builder
  (`sec:v1:idx:{endpoint}:{sha1}[:epoch]`), screener query canonicalization (AND-clause
  sorting, field-class detection for price/quarterly/annual/static).
- `rate_limit.py` — thread-safe token bucket.
- `cache.py` — Valkey wrapper: JSON get/set, `price_epoch`/`fund_epoch`, per-symbol
  `ver:idx:{symbol}`, single-flight lock, negative cache for 404s, `incr_with_expiry`
  (used by the gateway's rate limiter).
- `schema.sql` + `db.py` — Postgres tables for every INGEST/LAZY-ATOMIC data kind in the
  plan (symbol master, price/index/market-summary, quarterly-dates snapshot+diff,
  corporate actions, filings, news, suspensions, broker rankings, foreign flow, broker
  activity).
- `sectors_client.py` — real `httpx` client, one method per endpoint in the plan, 404
  negative-caching, never caches 429/5xx. **Endpoint paths in `ENDPOINTS` are
  placeholders (TODO)** — not yet verified against real Sectors API v2 docs.
- `repositories.py` — implements the actual strategies: `get_company_report` (CACHE,
  per-section, price-epoch vs symbol-version), `screen_companies` (CACHE, canonical
  query → top-200, sliced locally), `get_market_movers` (CACHE, price-epoch),
  `get_price_history` (INGEST-served, reads Postgres), `get_broker_activity`
  (LAZY-ATOMIC). All validate symbols against the symbol master first (credit
  protection).
- `deps.py` — shared lazy singletons (`get_db`, `get_cache`, `get_client`,
  `get_settings`) used by both `gateway/` and `ingest/`.

Verified by actually installing `redis`/`psycopg`/`httpx` in a scratch venv (Python 3.9
system default doesn't support `X | Y` unions — used Homebrew's `python3.14` instead) and
exercising canonicalization, the token bucket, and `psycopg.types.json.Json` for real.
Caught and fixed one real bug: `upsert_broker_rankings` mixed positional/named SQL params.

### 3. `ingest/` — jobs + scheduler
- `jobs/symbol_master.py` — refreshes 6 REFERENCE lists, sweeps the screener with
  `where={}` paging by offset (200/page) to rebuild `symbol_master`.
- `jobs/universe_close.py` — idempotent (no-ops if today already ingested), pulls daily
  close, upserts `price_daily`, bumps `price_epoch`.
- `jobs/quarterly_dates.py` — diffs quarterly-dates universe vs stored snapshot, bumps
  `ver:idx:{symbol}` per changed symbol + `fund_epoch` once.
- `scheduler.py` — APScheduler, Asia/Jakarta timezone: symbol master weekly (Mon 3am),
  universe-close poll every 5 min 16:00–19:00 (poll-until-today via idempotency + repeat
  invocation), quarterly-dates daily at 18:00.

Verified: imports clean, `build_scheduler()` constructs and lists all 3 jobs correctly
with a real `apscheduler` install.

### 4. `gateway/` — FastAPI + Strands, fully wired
- `registry.py` — loads `models.yaml`, builds real `AnthropicModel`/`OpenAIModel`
  instances (confirmed constructor signatures by inspecting the installed
  `strands-agents` SDK directly, not guessed).
- `agent.py` — builds a Strands `Agent` per request with the 4 MVP tools + a system
  prompt enforcing guardrails (tool-only data, cite `as_of`/source, no buy/sell advice).
- `guardrails.py` — per-user fixed-window rate limit (via `Cache.incr_with_expiry`) +
  not-financial-advice disclaimer appender.
- `compat.py` — OpenAI ⇄ Strands message conversion, SSE chunk/completion builders,
  title-generation short-circuit (**TODO**: exact LibreChat prompt string unconfirmed).
- `main.py` — bearer auth, `GET /v1/models`, `POST /v1/chat/completions` (streaming +
  non-streaming), model fallback on build error, rate limiting.
- `gateway/tools/*.py` — all 4 MVP tools (`get_company_report`, `screen_companies`,
  `get_price_history`, `get_market_movers`) call into `data/repositories.py`.

Verified end-to-end with `TestClient` + a mocked `Agent` + `fakeredis`: auth (401/200),
`/v1/models`, streaming SSE output, non-streaming JSON, disclaimer appended, title
short-circuit all produced correct output.

### 5. `scripts/manage.py` — manual CLI for testing against a real Sectors API key
Bypasses the scheduler so ingest jobs and cache-backed reads can be triggered on demand:
`init-db`, `run-job {symbol_master,universe_close,quarterly_dates}`, `get-report`,
`get-price-history`, `get-movers`, `screen`. Verified: argparse dispatch resolves to the
right handler for every subcommand, and `get-movers` runs the real repository code path
(cache key build → epoch lookup → single-flight lock → mocked client call) against
fakeredis — it only fails at lock *release*, a known fakeredis Lua/EVALSHA limitation,
not a bug (real Redis/Valkey supports this).

**To test with your own key:** put real values in `.env` (see "Before running anything"
above), `export $(cat .env | xargs)` or similarly load them into the shell, then:
```
python scripts/manage.py init-db                     # creates tables, needs Postgres running
python scripts/manage.py run-job symbol_master        # needed before screen/get-report/get-price-history work (symbol validation)
python scripts/manage.py run-job universe_close       # needed before get-price-history has any rows
python scripts/manage.py get-report BBCA overview
python scripts/manage.py screen '{"sector": "Banks"}' market_cap
```
Needs Postgres and Valkey reachable (e.g. `docker compose up postgres valkey`) — untested
against real instances of either in this session, only against fakeredis/mocks.

## Not started / open

1. ~~`db.init_schema()` is never called anywhere~~ — now callable via
   `python scripts/manage.py init-db`, but still not wired into any Compose startup step
   (a fresh `docker compose up` still won't have tables).
2. **`evals/promptfooconfig.yaml`** — still has a single `TODO` placeholder test; needs
   the ~15 real questions (infra doc section 13-14).
3. **Sectors API endpoint paths** (`data/sectors_client.py::ENDPOINTS`) are placeholders
   — must be checked against real API docs before this hits production traffic.
4. **`_build_agent_with_fallback`** only retries on `build_agent()` construction
   failure, not on a runtime error mid-stream from the primary model — worth deciding
   if that's good enough for MVP.
5. Never tested against a real Postgres/Valkey/Sectors API — only against fakeredis and
   mocked agents. No Docker daemon was available in this session to spin up the real
   compose stack.
6. `docker-compose.yml` hasn't been exercised (no `docker compose up` run yet). No chat
   UI service is defined in it yet — pending the LibreChat vs Open WebUI decision.
7. Confirm whether local changes made after the last commit (including anything from
   this checkpoint) have actually been committed — check `git status` on resume.

## Suggested next step
Either (a) wire up schema bootstrapping + bring up the full Compose stack for a real
end-to-end smoke test, or (b) fill in the promptfoo eval questions. Ask the user which.
