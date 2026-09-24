# IDX Agent — Progress Checkpoint

Resume point if this session is lost. Source specs: `idx_agent_infrastructure_diagrams_md.md`
(original infra architecture) and `sectors_idx_ingest_cache_plan_md.md` (per-endpoint
cache/ingest strategy) — the user holds these and may or may not have dropped them into
the repo yet, check before assuming they're missing. **The actual product target is now
`portfolio-intelligence-business-requirements-v1.1.md`** (a 5-role multi-agent design,
already in the repo), which supersedes the original 4-tool IDX Analyst MVP — see item 6
under "Done" below and `portfolio-intelligence-data-gap-analysis-v1.md` (also in the
repo) for the Sectors-API feasibility review against it.

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
  time. A **persistent project venv now exists at `.venv/`** (built with Homebrew's
  `python3.14`, `data/requirements.txt` + `requirements-dev.txt` installed) — use
  `.venv/bin/python`, not system `python3`.
- **`.env` now exists and has a real `SECTORS_API_KEY` + `SECTORS_API_BASE_URL`
  (`https://api.sectors.app/v2/`) set by the user.** `POSTGRES_HOST`/`VALKEY_HOST` are
  set to `localhost` (not the Docker service names `postgres`/`valkey` from
  `.env.example`) since `scripts/manage.py` runs on the host, not in a container.
  `POSTGRES_PASSWORD=devpassword` (a local-only placeholder, fine since Postgres isn't
  exposed beyond localhost). `.gitignore` now excludes `.env`, `.venv/`, pycache, etc.
  `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`IDX_GATEWAY_KEY` are still empty — not needed
  for anything done so far (gateway/agent work), only for `data/`+`ingest/` testing.
- **Postgres and Valkey are running for real** via `docker compose up -d postgres
  valkey`, with ports published (`5432`, `6379`) so the host-run CLI can reach them —
  `docker-compose.yml` didn't have these published originally, fixed this session.
  Schema applied for real via `python scripts/manage.py init-db` — 11 tables exist.
- **`models.yaml` still has `model_id: TODO` for both entries.** Loading the registry
  works fine (nothing validates the value), but building a real model will fail or
  hit a nonexistent model until real Anthropic/OpenAI model IDs are filled in. Not
  touched this session (no gateway/agent work happened here).

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
- `sectors_client.py` — real `httpx` client. **All endpoint paths are now verified
  live against the real API (2026-09-24), not guessed — see item 7 below.** `where`
  is a SQL-like string (Sectors' own format), not a JSON dict as originally assumed.
- `repositories.py` — implements the actual strategies: `get_company_report` (CACHE,
  per-section, price-epoch vs symbol-version), `screen_companies` (CACHE, canonical
  query → top-200, sliced locally), `get_price_history` (INGEST-served, reads
  Postgres), `get_broker_activity` (LAZY-ATOMIC), `ensure_price_detail` (new,
  LAZY-ATOMIC — backfills volume/market_cap the bulk close feed lacks). All validate
  symbols against the symbol master first (credit protection). `get_market_movers`
  was removed in the business-doc pivot (item 6) — no requirement maps to it.
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
`get-price-history`, `screen`. Verified: argparse dispatch resolves to the right handler
for every subcommand, and the repository code path (cache key build → epoch lookup →
single-flight lock → mocked client call) runs correctly against fakeredis — it only
fails at lock *release*, a known fakeredis Lua/EVALSHA limitation, not a bug (real
Redis/Valkey supports this).

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

### 6. Pivot to the Portfolio Intelligence business requirements
The user shared `portfolio-intelligence-business-requirements-v1.1.md` (a 5-role
multi-agent design: Chief Orchestrator, Investment Research Lead, Portfolio Risk Lead,
Market/Event Intelligence Lead, Independent Risk and Evidence Officer, plus Appendix A's
calculation conventions and Appendix B's review thresholds). This supersedes the
original 4-tool IDX Analyst MVP as the actual product target.

- **`portfolio-intelligence-data-gap-analysis-v1.md`** — feasibility review of whether
  Sectors alone can support it. Verdict: yes for section 9's "Must" demo scope, except
  two real gaps (documented with proposed scope reductions): controlling-group/corporate
  ownership mapping (Sectors gives per-company shareholders, not a cross-company group
  taxonomy — proposed fix: hand-curated mapping table for the demo universe) and macro
  inputs like FX/rates (out of scope for an IDX equities API — the business doc already
  permits treating these as labeled scenario assumptions instead). Also flags 3
  `UNVERIFIED` items (bank-specific ratio field coverage, price-adjustment convention,
  consolidation-basis labeling) that need checking against real Sectors field docs
  before those specific features are built.

- **Sectors API surface pruned** to only endpoints that map to a requirement in the
  business doc (see the mapping in the gap analysis and the comment block at the top of
  `data/sectors_client.py`). Removed: per-symbol/index daily transaction (both dead —
  INGEST-served, never actually called), market summary, index daily close, top movers,
  most traded, listing performance, broker-activity-by-code, top buyers/sellers, top
  accumulation/distribution, net-foreign-inflow-per-symbol. This also removed the
  `get_market_movers` gateway tool/repository function (no business requirement maps to
  it — unusual-move detection per Appendix B is computed from the ingested price store
  instead) and 3 now-dead schema tables (`index_daily`, `market_summary_daily`,
  `broker_activity_by_code`). Verified: full import chain (gateway, ingest, CLI) still
  resolves correctly after the prune, `get-movers` CLI subcommand confirmed gone.

- **`analysis/` — new package, the calculation engine (Appendix A), built first per
  section 8's "use ordinary code for arithmetic" principle and the user's own choice of
  build order.** Pure Python, no LLM, no I/O: `portfolio.py` (position/portfolio value,
  weights, category exposure, HHI), `returns.py` (returns, cash-flow-adjusted wealth
  index, drawdown), `liquidity.py` (ADV20, normal/stressed exit days, free-float
  capacity), `fundamentals.py` (margins, ROA/ROE, debt ratios, cash conversion, plus
  bank-specific NIM/NPL/LDR/cost-to-income/capital-adequacy), `valuation.py` (P/E, P/B,
  EV/EBITDA, FCFF/FCFE, terminal value), `flow.py` (normalized foreign flow, broker
  gross value, Top-K share, net imbalance), `scenario.py` (fixed-weight scenario
  return/loss/P&L only — covariance/beta/VaR/ES deliberately deferred, matching the
  business doc's own "Later" priority tier for those). `types.py` defines the
  `Unavailable`/`NM` sentinels Appendix A requires ("neither should become zero") and
  every function follows the doc's explicit edge-case rules verbatim (e.g. negative
  equity → ROE is `NM`; a discount rate not exceeding the terminal growth rate raises
  `InvalidValuationAssumption`, matching Appendix C's rejection acceptance case; a zero
  ADV20 → exit days is `Unavailable`, never `0`).

  55 tests in `analysis/tests/`, one file per module, encoding the qualitative rules
  from the doc's prose (not just the bare formulas) — run with real `pytest` in a
  Python 3.14 venv: all 55 pass. `requirements-dev.txt` (`pytest`) added at repo root.

  **Not built yet:** the 5 agent roles themselves, the orchestrator, the independent
  reviewer's PASS/REVISE/DATA BLOCKED/HUMAN ESCALATION workflow, Appendix B's review
  thresholds/materiality scoring. **Wiring to real data started this session — see
  item 7.**

### 7. Wired to the real Sectors API for the first time — major corrections found
The user set a real `SECTORS_API_KEY`. Rather than keep guessing endpoint paths, every
path in `data/sectors_client.py::ENDPOINTS` was verified with actual live requests
(`httpx` probe scripts run via Bash, throwaway files in the scratchpad, deleted after).
**Do not trust `https://docs.sectors.app/llms.txt`** — it was fetched once via WebFetch
and came back with a plausible-looking but almost entirely fabricated endpoint list
(namespaced paths like `/v2/indonesia/screener/companies` that all 404). Only paths
independently confirmed by a real request are in `ENDPOINTS` now.

**Corrections that change how everything above actually behaves:**
- Screener `where` is a **SQL-like string** (`"sector='Financials'"`), not a JSON
  dict — `data/canonical.py`'s entire canonicalization design was rewritten (dict of
  field→value → string, whitespace-normalized only, no reordering/case-folding since
  that risks corrupting a live SQL-like string). `screener_field_classes` now
  regex-extracts field names from the string instead of reading dict keys.
- All list-returning endpoints wrap results as `{"results": [...], "pagination":
  {"has_next", "next_offset", ...}}`, not a bare list — `repositories.screen_companies`
  and `ingest/jobs/symbol_master.py`'s sweep both fixed to unwrap this and paginate via
  `has_next`/`next_offset` rather than a `len(page) < page_size` guess.
- **The bulk daily-close feed (`close/`) has ONLY symbol/date/close — no volume, no
  market cap** (confirms the ⚠ already flagged in the original ingest plan doc) — and
  is hard-capped at **30 rows/page** regardless of requested `limit` (962 symbols ≈ 33
  pages/day). `ingest/jobs/universe_close.py` now paginates and leaves those columns
  NULL. The per-symbol `daily/{symbol}/` endpoint — previously pruned as "dead"/
  INGEST-served, which was **wrong** — actually has full OHLCV + market cap and is now
  `data/repositories.py::ensure_price_detail`, a new LAZY-ATOMIC backfill function.
- `include_query_values` only returns fields actually referenced in `where`/`order_by`
  — a bare sweep returns just symbol/company_name. `symbol_master`'s ingest sweep now
  uses an always-true compound `where` (`sector!='' and sub_sector!='' and ...`) purely
  to force sector/sub_sector/industry/sub_industry into every row.
- Real field names differ from assumed schema column names: API uses `sub_sector`/
  `sub_industry` (with underscore), our Postgres columns stay `subsector`/`subindustry`
  — just a mapping detail in `_row_to_symbol_master`, not a schema change.
- Company Report's real section list (confirmed): `overview, valuation, future, peers,
  financials, dividend, management, ownership`. Bank-specific ratios (NIM, NPL,
  loan-to-deposit, capital adequacy, CASA) exist, but only inside `financials`'
  `historical_financial_ratio` array per year — **resolves gap G4** from the gap
  analysis doc (update that doc's G4 status; not yet done). Consolidation basis is
  still not labeled anywhere — **G6 is now a confirmed real gap, not just unverified.**
  `listing_date` exists per-symbol in `company_report`'s `overview` section (confirmed
  via a real BBCA call) but is NOT in the bulk screener sweep — left `None` in
  `symbol_master` rather than doing 962 per-symbol calls to populate it.
- **Found and fixed a real, pre-existing bug**, unrelated to any of the above: every
  `conn.executemany(...)` call in `data/db.py` was wrong — `psycopg.Connection` has no
  `executemany`, only `Cursor` does. This bug existed since `db.py` was first written
  and was never caught because nothing had run against real Postgres until this
  session. Fixed via a new `Database._executemany(conn, sql, params)` helper wrapping
  `conn.cursor()`, used at all 9 call sites.
- Also fixed: `docker-compose.yml`'s `postgres`/`valkey` services had no published
  ports, so the host-run CLI couldn't reach them (fine for the full in-Docker stack,
  broken for local CLI testing) — added `5432:5432` and `6379:6379`.

**Still genuinely unresolved** (confirmed by direct probing, not just unguessed):
revenue segments / companies-with-revenue-segments (every path guess 404), and —
more importantly — **the quarterly-dates change-detector's bulk feed**
(`ingest/jobs/quarterly_dates.py`). No working endpoint or screener field was found
(every guessed screener field name came back `400 INVALID_WHERE_CLAUSE`, a real
"field does not exist" error, not a syntax problem). That job now raises
`NotImplementedError` with a clear message instead of silently failing, and is
unscheduled in `ingest/scheduler.py`. Open design question left for whoever picks
this up: redesign around polling `get_quarterly_financials(symbol)` per symbol
(check the credit cost of a 962-symbol sweep first) or find the real bulk endpoint
by checking Sectors' actual dashboard/docs rather than further guessing paths.

**Verified for real, end to end, with the live API + real Postgres/Valkey:**
`scripts/manage.py init-db` (11 tables created), `run-job symbol_master` (962 symbols
ingested with correct sector/subsector/industry/subindustry, 5 reference lists
populated), `run-job universe_close` (correctly no-ops when today's close hasn't
landed yet — verified separately that the pagination loop fetches all 962 rows for a
past date), `get-report BBCA overview,valuation` (real data back, second call
confirmed cache-hit-fast), `backfill-price BBCA` (21 days of real OHLCV+market_cap
landed in Postgres), `screen "sector='Financials'" --order-by=-market_cap` (correct
results, correctly sorted). Also fixed a `scripts/manage.py` CLI bug found along the
way: `order_by` as a positional argument broke on values starting with `-` (argparse
treats `-market_cap` as an unrecognized flag) — changed to `--order-by=` (the `=` form
sidesteps argparse's flag-parsing).

## Item 8: gap analysis doc updated, analysis/ wired to real ingested data (2026-09-24)

Two follow-ups from item 7, done with **zero new Sectors API calls** — everything
below reads only already-ingested Postgres data or already-cached Valkey data.

- **`portfolio-intelligence-data-gap-analysis-v1.md` updated**: G4 (bank-specific
  ratio fields) moved from `UNVERIFIED` to resolved-available — `historical_financial_ratio`
  inside the `financials` report section has them, confirmed in item 7. G6
  (consolidation basis) moved from `UNVERIFIED` to a confirmed real gap — neither
  `financials` nor `financials/quarterly/{symbol}/` labels consolidated vs. standalone
  anywhere. Added a new **G7** for the quarterly-dates change-detector gap (item 7's
  finding) with a proposed scope reduction (poll watchlist symbols individually rather
  than a full-universe daily sweep).
- **`data/analysis_bridge.py`** (new): the only I/O adapter between `analysis/`'s pure
  functions and real data. Wires `analysis/portfolio.py` (position/portfolio
  value, weights, HHI, effective holdings), `analysis/returns.py` (day-over-day price
  returns, drawdown, max drawdown), and `analysis/liquidity.py` (ADV20, normal exit
  days) to `price_daily` rows already sitting in Postgres from `universe_close` /
  `backfill-price`. Deliberately does **not** wire `analysis/fundamentals.py` or the
  raw-component side of `analysis/valuation.py` (pe/pb/fcff/fcfe from net income, book
  equity, EBIT, etc.) — those need the company report's `financials` section, which
  has not been fetched for any symbol yet (costs a credit per section; see gap
  analysis G4's "status of the wiring itself" note).
- **Found and fixed a real bug during wiring**: psycopg returns Postgres `NUMERIC`
  columns as `Decimal`, which doesn't mix arithmetically with the plain `float`s
  `analysis/`'s functions expect (`TypeError: unsupported operand type(s) for *:
  'decimal.Decimal' and 'float'` in both `liquidity.normal_exit_days` and
  `returns.cash_flow_adjusted_wealth_index`). Fixed by casting `close`/`volume` to
  `float` in `analysis_bridge.py` at the DB-read boundary — the right layer for this,
  since `analysis/` is deliberately DB-agnostic and shouldn't know about `Decimal`.
- Added `Database.get_latest_close(symbol)` for portfolio valuation.
- New CLI commands: `analyze-portfolio`, `analyze-liquidity`, `analyze-returns` (all
  Postgres-only, no API calls, see their `--help` text). New gateway tools in
  `gateway/tools/portfolio_analysis.py`, added to `MVP_TOOLS` in `gateway/agent.py`.
- New tests: `data/tests/test_analysis_bridge.py`, 4 integration tests against the
  real local Postgres (skips cleanly if unreachable or under-provisioned), run against
  the real BBCA data ingested in item 7. All 59 existing `analysis/tests` still pass
  unchanged. Verified live via `scripts/manage.py analyze-*` against real BBCA data —
  output sanity-checked (e.g. `analyze-returns BBCA 1m` correctly reports 21 sessions
  and a max drawdown of ≈ -8.5%, matching the actual ingested price swing).

## Item 9: fundamentals/valuation wired to real data — 1 credit spent (2026-09-24)

Per the user's explicit go-ahead to spend credit here as long as it's efficient: spent
exactly **1 credit** (one `company/report/BBCA/` call for the `financials` section —
`overview` was already cached from item 7) to wire the previously-deferred half of the
calculation engine.

- **`data/analysis_bridge.py::fundamentals_snapshot`** (new): general fundamentals
  (revenue growth, margins, ROA/ROE, net debt/EBITDA, interest coverage, CFO margin,
  cash conversion) from `historical_financials`' latest fiscal year (and prior year,
  for the two ratios needing an average); bank-specific ratios (NIM, gross NPL ratio,
  loan-loss coverage, loan-to-deposit, cost-to-income, capital adequacy) when
  loan/deposit/NII fields are present; raw-component valuation (P/E, P/B, EV/EBITDA,
  FCFF, FCFE).
- **Validated three field-mapping choices against Sectors' own precomputed
  `historical_financial_ratio`** for BBCA FY2025 (a free cross-check — that section
  was already in the same cached payload): capital adequacy matched immediately
  (tier1+tier2 / RWA). Loan-to-deposit and NIM did **not** match on the first,
  more-obvious field choice (`gross_loan` and `gross_loan + non_loan_earning_assets`
  respectively) — corrected to `net_loan` and `non_loan_earning_assets` alone, which
  then matched Sectors' reported ratios to 6+ decimal places. `non_performing_loans`
  has no named field at all; parsed from a human-readable label inside
  `industry_breakdown.loan_at_risk`, confirmed only for BBCA — flagged in
  `_provenance` as needing verification before trusting for other banks.
- FCFF/FCFE correctly return `Unavailable` for BBCA — there is no
  change-in-operating-working-capital field anywhere in `historical_financials` — this
  is the calculation engine behaving exactly as designed (Appendix A: never
  approximate a missing input), not a defect.
- New CLI command `analyze-fundamentals`, new gateway tool `analyze_fundamentals`,
  both added alongside item 8's tools.
- New test `test_fundamentals_snapshot_matches_sectors_reported_ratios` in
  `data/tests/test_analysis_bridge.py` — asserts the computed bank ratios against
  Sectors' own reported values (exact match) and that FCFF/FCFE are `Unavailable`.
  Skips cleanly (no live call) if BBCA's `financials` section isn't cached. 60/60
  tests pass.
- Updated `portfolio-intelligence-data-gap-analysis-v1.md`'s G4 "status of the wiring
  itself" note and `README.md` to reflect this.

**Not wired for any symbol other than BBCA** — the field-mapping corrections above
were validated against one bank; a non-bank issuer's `historical_financials` shape
(and possibly a different subset of populated fields) hasn't been checked, and a
second bank hasn't been used to confirm the NIM/loan-to-deposit mappings generalize
beyond BBCA specifically.

## Item 10: multi-agent architecture started — Chief + Investment Research Lead (2026-09-24)

User picked "Orchestrator + Investment Research Lead first" over building all 5 roles
at once or stubbing all of them — a working vertical slice that can be tested
end-to-end as soon as a model key exists, rather than a fully-built but unverified
5-role graph.

- Installed `strands-agents` (and the rest of `gateway/requirements.txt`) into
  `.venv` — wasn't installed before this point in the session.
- **`gateway/roles/investment_research.py`** (new): builds the Investment Research
  Lead as its own `Agent`, tools = `get_company_report`, `get_price_history`,
  `analyze_fundamentals`, `screen_companies`. System prompt covers 3 of its 4
  business-doc functions (fundamentals, valuation, and — honestly — the absence of
  ownership/governance and thesis-monitoring tooling) and states its boundary: does
  not decide portfolio allocation.
- **`gateway/roles/orchestrator.py`** (new): builds the Chief with no tools of its
  own except the Investment Research Lead wired in via Strands' built-in
  `Agent.as_tool()` (`strands/agent/_agent_as_tool.py` — didn't need to hand-roll
  this). System prompt explicitly lists Portfolio Risk Lead, Market and Event
  Intelligence Lead, and the Independent Risk and Evidence Officer as **not yet
  available**, instructing the model to say so rather than imply a portfolio-risk
  check or independent review happened — same anti-fabrication principle the data
  layer already follows, applied to missing roles instead of missing fields.
- **Deliberate capability regression, documented**: the flat MVP agent this replaces
  had direct access to `analyze_portfolio`/`analyze_liquidity`/`analyze_returns`.
  Those are Portfolio Risk Lead's territory per the role table, so the Chief does not
  get them directly — they're still exposed as CLI commands and importable tools,
  just not attached to the live agent graph until that role exists.
- **`gateway/agent.py`** rewritten to a 12-line re-export of
  `gateway.roles.orchestrator.build_agent` — `gateway/main.py` needed zero changes
  (same import, same signature).
- Both roles currently share one model (whatever `/v1/models` selects) — per-role
  model tiering (section 8's "modest reasoning for routine coordination, stronger
  models for disputed conflicts") is not implemented.
- **Verified structurally, not live**: both agents construct without error and wire
  their tools/sub-agent as expected, using a placeholder API key and model id (no
  network call happens at construction time). Could not verify an actual
  conversation — no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is set, and `models.yaml`
  still has `model_id: TODO` for both registry entries. All 60 `data`/`analysis`
  tests still pass unaffected.

## Item 11: tracing (Phoenix) + Arize eval script — verified live, no LLM involved (2026-09-24)

User asked for observability so the agents are "already traced" once a real LLM key
is added, plus an Arize-based evaluation pass. Both built and verified against a
real, freshly-started Phoenix container this session — with zero LLM calls (a
deliberately invalid Anthropic key was used to test the failure path), since no
ANTHROPIC_API_KEY/OPENAI_API_KEY exists yet.

- **`gateway/telemetry.py`** (new): `setup_telemetry()` configures the global OTel
  tracer provider with an OTLP/HTTP exporter pointed at Phoenix — this is the only
  thing needed for Strands' own spans (every `Agent` calls `get_tracer()`
  unconditionally in `strands/agent/agent.py`) to start exporting; no per-agent
  changes needed. `traced_conversation()` wraps each gateway request in one
  additional span tagged with OpenInference's `input.value`/`output.value`/
  `openinference.span.kind=AGENT` — a flat, query-friendly shape Strands' own
  `gen_ai.*`-convention spans don't provide, and what `evals/phoenix_evals.py`
  actually reads.
- **Real bug found and fixed during verification, not assumed from docs**: Phoenix
  buckets traces into projects by the `openinference.project.name` resource
  attribute — confirmed by first trying the obvious thing (`service.name`, which is
  what Strands' `StrandsTelemetry()` sets by default) and watching every trace land
  under Phoenix's `"default"` project regardless of that value. Fixed by building a
  custom `Resource` with both attributes and handing it to
  `StrandsTelemetry(tracer_provider=...)` — which in turn required calling
  `trace.set_tracer_provider()` explicitly, since that constructor path does *not*
  register the provider globally on its own (only its no-arg branch does), and
  Strands' own `Tracer` reads the global provider at construction time.
  Re-verified after the fix: a synthetic span landed under a real
  `idx-agent-gateway` project with exactly the expected columns.
- `gateway/main.py`: calls `setup_telemetry()` at module import (process startup);
  wraps both the streaming and non-streaming `chat_completions` paths in
  `traced_conversation`; added a `shutdown` handler that force-flushes the batched
  span exporter so a request right before process exit isn't lost to timing.
  `gateway/compat.py::latest_user_text` extracts the question text for the span.
- `docker-compose.yml`: `agent-gateway` now depends on `phoenix` and gets
  `PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006` (the in-network name) instead of
  the localhost default meant for running the gateway outside Compose.
- **`evals/phoenix_evals.py`** (new): pulls traced conversations from Phoenix via
  `arize-phoenix-client`, grades them with an LLM judge
  (`arize-phoenix-evals`'s `create_classifier`/`evaluate_dataframe`) against 3
  project-specific compliance checks (cites a date for cited figures, no buy/sell/
  hold language, discloses missing data/unimplemented roles rather than glossing
  over them) — deliberately not generic hallucination/toxicity templates, since
  those don't check what this product's business doc actually requires.
- **Real bug found and fixed here too**: `evaluate_dataframe`'s own docstring says
  its result columns are "JSON-serialized", but a live run (with the deliberately
  invalid key) showed `execution_details` come back as a plain `dict` already, not
  a JSON string — `json.loads()` on it crashed with `TypeError`. Fixed with a
  `_parsed()` helper that accepts either shape, since the score column's actual
  shape on a successful judge call couldn't be verified without a real key.
- Confirmed working end-to-end apart from the judge call itself: real traces
  fetched, correct columns extracted, evaluator dispatched, and the script reports
  "N evaluator call(s) failed" cleanly rather than crashing when the (deliberately
  bad) judge key fails every call.
- New deps: `gateway/requirements.txt` gained `opentelemetry-exporter-otlp-proto-http`
  and `openinference-semantic-conventions`; new `evals/requirements.txt`
  (`arize-phoenix-client`, `arize-phoenix-evals`). Both installed into `.venv` and
  used for the verification above.

**Not yet done**: running either the tracing or the eval script against a real
conversation — both are blocked on the same missing `ANTHROPIC_API_KEY`/
`OPENAI_API_KEY`/`model_id` as item 10. The `evals/promptfooconfig.yaml` placeholder
(open item 2 below) is unrelated and still unfilled.

## Item 12: first real LLM call — tracing confirmed working end to end (2026-09-24)

User added a real `ANTHROPIC_API_KEY`. Before running anything, filled in
`models.yaml`'s `idx-analyst-claude` entry (still `TODO` until now) with
`claude-haiku-4-5-20251001` — deliberately Haiku, not Sonnet, since the user
explicitly flagged limited Anthropic credit and this was the first-ever real call
against this codebase; tier changed `standard` -> `cheap` to match.

**Two real `.env` bugs found and fixed before the test could even run:**
- `IDX_GATEWAY_KEY` was empty — the gateway's own auth check would reject every
  request. Set to a local dev secret (`dev-local-gateway-key-2a9f7e1c4b` — not
  sensitive, never leaves localhost).
- `PHOENIX_COLLECTOR_ENDPOINT` was set to `http://phoenix:6006` (the in-Docker
  service name) while every other `.env` value (`POSTGRES_HOST`, `VALKEY_HOST`) is
  `localhost` — i.e. configured for running the gateway locally, not inside
  Compose. That mismatch would have silently made tracing a no-op (DNS failure on
  an async exporter, swallowed rather than crashing). Fixed to `http://localhost:6006`;
  `docker-compose.yml`'s own `environment:` override (item 11) still supplies the
  in-Docker value when the gateway itself runs in Compose.

**The test**: started `uvicorn gateway.main:app` locally, confirmed `/v1/models`
auth works, then sent exactly one minimal, deliberately tool-free question ("In one
short sentence, what is your role?") to `idx-analyst-claude` — chosen specifically
to avoid triggering any Sectors API tool call, so only Anthropic credit was spent,
once. Real response came back from the Chief Portfolio Intelligence Orchestrator,
correctly in-character and correctly appending the not-financial-advice disclaimer.

**Tracing confirmed working, not just structurally correct**: queried Phoenix
after the call and found the real conversation's `chat_completion` span (question
+ full answer, matching item 11's design) plus Strands' own detailed spans
(`chat`, `execute_event_loop_cycle`, `invoke_agent
chief_portfolio_intelligence_orchestrator`) — with **real token counts attached**:
1,079 prompt tokens + 32 completion tokens = 1,111 total, correctly tagged with
`claude-haiku-4-5-20251001`. This answers the user's actual concern directly: every
call's real cost is now visible per-conversation in Phoenix's UI
(`http://localhost:6006`, project `idx-agent-gateway`), not just logged as an
opaque total.

Gateway process stopped after the test (no need to leave it running). `evals/
phoenix_evals.py` has not been run against this real trace yet — that would cost
one more small judge call; not done without checking with the user first, per the
same credit-consciousness this whole item was about.

## Item 13: both remaining verifications run — tool-call path + real eval pass (2026-09-24)

User said "run both": item 12's suggested next steps (a) and (b) together.

**(b) Tool-triggering question, full multi-agent path, zero new Sectors credits:**
Asked "Briefly, how are BBCA fundamentals looking based on its latest reported
fiscal year?" — BBCA's `overview`/`valuation`/`financials` report sections were
already cached from earlier sessions, so this was chosen deliberately to exercise
the real delegation + tool-call path without spending Sectors credit. Confirmed via
`valkey-cli keys` before/after: identical cache key set, zero new keys — the
prediction held. The real answer correctly cited NPL/ROE/capital-adequacy/
loan-to-deposit figures matching item 9's validated field mappings. The resulting
Phoenix trace shows the exact delegation chain for the first time:
`chat_completion` -> `invoke_agent chief_portfolio_intelligence_orchestrator` ->
`execute_tool investment_research_lead` -> `invoke_agent investment_research_lead`
-> `execute_tool analyze_fundamentals` -> final `chat`. This is the first live
confirmation that the Chief's `Agent.as_tool()` delegation (item 10) actually works
end to end, not just structurally.

**(a) `evals/phoenix_evals.py` against real traces:** Ran with `--model
claude-haiku-4-5-20251001` as judge (same cheap-model reasoning as item 12) against
the 4 real/synthetic traces now in Phoenix. All 12 classifier calls (3 classifiers ×
4 conversations) completed successfully this time.

- **Real bug found and fixed while reading the output, not from docs**: the script's
  own failure counter reported "12 evaluator call(s) failed" even though every
  score looked correct — `evaluate_dataframe`'s docstring claims a `"success"`
  status string; the real one observed live is `"COMPLETED"`. Fixed the check to
  match on the real value (and added `"DID NOT RUN"` as a failure state, seen
  earlier in item 11's broken-key test). Re-ran after the fix: no false failure
  message.
- **A genuine, actionable finding from the eval itself** (not a script bug): the
  BBCA fundamentals answer was graded `cites_evidence_date: undated` — the answer
  cited NPL/ROE/capital figures without stating the fiscal year, even though
  `analyze_fundamentals`'s own output includes `fiscal_year: 2025`. This is exactly
  the kind of gap section 5's "state the fiscal year or as_of date" requirement is
  meant to catch, and `gateway/roles/investment_research.py`'s system prompt
  already says to do this — worth tightening that prompt (or adding a stricter
  instruction) since the model isn't reliably following it yet. Not fixed this
  session — flagged as a real finding for whoever iterates on the prompt next.
- `--log-annotations` tested too: 12 annotations written back to Phoenix and
  independently re-read via `get_span_annotations_dataframe` to confirm they
  persisted correctly against the right spans.

Gateway stopped after both tests. All 60 `data`/`analysis` tests still pass,
unaffected.

## Item 14: found and fixed a real tracing gap — eval judge calls were invisible (2026-09-24)

User reported a real discrepancy: Phoenix showed ~8.3k tokens tracked total (item 13's
5 real gateway LLM calls, confirmed by summing `attributes.llm.token_count.total`
across all `chat` spans), but their own Anthropic dashboard showed **32k tokens /
$0.05** actually billed. The ~24k gap was traced to a real bug, not user error.

- **Root cause, confirmed live**: `evals/phoenix_evals.py` (run twice in item 13 —
  24 real judge calls total: 3 classifiers × 4 conversations × 2 runs) never called
  `gateway/telemetry.py::setup_telemetry()`. It's a standalone script, not part of
  the gateway process, so nothing configured its OpenTelemetry tracer provider —
  every one of those 24 real, billed Anthropic calls went completely untracked, in
  any project. Confirmed by checking Phoenix's project list before the fix: only
  `idx-agent-gateway` and `default` existed; the eval script's calls appeared
  nowhere.
- **Fix**: `evals/phoenix_evals.py` now calls `setup_telemetry()` itself, under a
  separate `idx-agent-evals` Phoenix project by default (`PHOENIX_PROJECT_NAME`
  override) so judge-call cost stays visually distinct from real user conversations
  while still being fully tracked. `phoenix.evals` (like Strands) auto-instruments
  against whatever global tracer provider is configured
  (`phoenix.evals.tracing.get_tracer()`), so no other wiring was needed for the
  calls to start appearing at all.
- **Second gap found immediately after fixing the first, by actually checking, not
  assuming success**: the judge call *did* now appear in Phoenix (real input/output
  visible) but with **no token-count attributes at all** — `phoenix.evals`' own
  span (`LLM.generate_object`) doesn't capture usage; only the underlying SDK call
  does. Fixed by adding `openinference-instrumentation-anthropic` (and
  `-openai`, for symmetry, since the same gap would hit an OpenAI-judged run) to
  `setup_telemetry()` — this patches the Anthropic/OpenAI SDK clients directly to
  record real token usage on every call, Strands-orchestrated or not. Re-verified:
  a `messages.create` child span now carries real `attributes.llm.token_count.*`
  (e.g. 918 prompt + 178 completion = 1,096 for one judge call).
- Added `sys.path.insert(...)` to `evals/phoenix_evals.py` (matching
  `scripts/manage.py`'s existing pattern) — needed once the script started
  importing `gateway.telemetry`, since running it directly (`python
  evals/phoenix_evals.py`) doesn't put the repo root on `sys.path` otherwise.
- Verified the complete fixed pipeline end to end with `--limit 1` (3 real calls,
  minimal spend): all 3 classifiers succeeded, all 3 now show up in Phoenix's
  `idx-agent-evals` project with real token counts (1,101 + 1,098 + 1,133 = 4,428
  tokens for that run — visible, not a black box).
- New deps: `gateway/requirements.txt` gained `openinference-instrumentation-anthropic`
  and `openinference-instrumentation-openai`. `evals/requirements.txt` now notes it
  needs `gateway/requirements.txt` installed alongside it, since the eval script
  imports `gateway.telemetry`.

**Net effect**: every Anthropic/OpenAI call this codebase can make — gateway
conversations AND eval judge calls — is now traced with real token counts, in one
of two clearly-separated Phoenix projects. This was a genuine, user-caught gap, not
a hypothetical one; item 13's claim that the eval run was "small" undercounted its
real cost by not accounting for the fact that it wasn't traced at all.

## Item 15: eval results now default to Phoenix's UI, not just terminal output (2026-09-24)

User asked for a UI a non-technical user could read eval results in. Phoenix
already provides exactly this once annotations are logged (confirmed via its
GraphQL schema — `getProjectByName`/`annotationConfigs` back the per-project
annotation summaries and per-trace annotation panels its UI renders) — the gap was
that `--log-annotations` was opt-in. Flipped: `evals/phoenix_evals.py` now logs
annotations **by default** (`--no-log-annotations` to skip). A reviewer can now open
`http://localhost:6006`, pick the `idx-agent-gateway` project, and read
`cites_evidence_date`/`no_investment_recommendation`/`discloses_missing_data`
results directly in the trace list and per-trace detail view — no script output,
no separate dashboard to build.

## Item 16: session management (Valkey) + long-term memory (Postgres) — verified live (2026-09-24)

User asked, before continuing to build more agents: can Valkey do session
management and per-user memory (short-term and long-term)? Answer given: Valkey is
a good fit for short-term (TTL-based, fine to lose under memory pressure), but
Valkey's own `--maxmemory-policy allkeys-lru` (docker-compose.yml) makes it the
wrong place for anything that must actually persist — a silently-evicted portfolio
or recorded thesis would be a real correctness bug, not just a slowdown. Long-term
went to Postgres instead. When asked what long-term memory should concretely store,
the user said "anything user related should be configurable" — so it's free-form
text facts, not a fixed schema of typed columns.

**Used Strands' own native subsystems instead of hand-rolling**, discovered by
checking the library before assuming nothing existed: `strands.session.
RepositorySessionManager` + a custom `SessionRepository` for short-term, and
`strands.memory.MemoryManager` + a custom `MemoryStore` for long-term — both are
first-class Strands abstractions with well-defined protocols, giving the Chief
Strands' own `search_memory`/`add_memory` tools for free rather than hand-rolled
ones.

- **`data/session_repository.py`** (new): `ValkeySessionRepository`, a full
  implementation of Strands' `SessionRepository` abstract class, backed by Valkey
  with a sliding 4-hour TTL (refreshed on every write). Wired via
  `RepositorySessionManager` into the Chief only — not Investment Research Lead,
  which is wrapped via `.as_tool()` with `preserve_context=False`, and Strands
  explicitly forbids combining that with a session_manager (a real constraint
  found while implementing, not assumed).
- **`data/memory_store.py`** (new): `PostgresUserMemoryStore`, implementing
  Strands' `MemoryStore` protocol against the new `user_memory` table
  (`data/schema.sql`) — free-form text facts + JSONB metadata per user_id.
  Deliberately no automatic background extraction (`extraction=False`) and no
  automatic context injection (`MemoryManager(injection=False)`) — both would add
  real per-call token cost regardless of relevance, cutting against this project's
  established credit-consciousness; the Chief only pays for memory when it (or the
  user) explicitly calls `add_memory`/`search_memory`.
- **`gateway/main.py`**: added `X-Session-Id` request/response header handling. A
  session Valkey has already seen gets only the newest message forwarded to the
  agent (history is restored server-side); an unseen or absent one gets the
  client's full message array once, seeding the session. `gateway/roles/
  orchestrator.py::build_agent` now takes `db`, `cache`, `user_id`, `session_id`
  and wires both subsystems in.
- **Two real bugs found via live testing, not assumed correct from a first pass**:
  1. `search_user_memory`'s first implementation used `ILIKE '%<whole query>%'`
     directly on the query string — a real gateway conversation showed the model
     calls `search_memory` with natural-language phrases ("BBCA holdings shares
     position"), which essentially never appears as one contiguous substring in
     stored text ("User holds 1000 shares of BBCA") despite every word being
     present.
  2. Switching to Postgres full-text search (`plainto_tsquery`) didn't fix it
     either — `plainto_tsquery` ANDs every word together, so the query's 4th word
     ("position", absent from the stored fact) zeroed out an otherwise-good match.
     Fixed by building an OR-query across the query's own words instead (`" | "
     .join(words)` into `to_tsquery`), ranked by `ts_rank` — verified directly
     against Postgres with the exact failing query strings before re-testing live.
  3. A related process mistake, not a code bug: re-tested through a gateway
     process that had been started *before* the `db.py` fix, without `--reload` —
     got the same wrong (empty) answer again and briefly suspected the fix hadn't
     worked, until noticing the process was running stale code. Restarted, correct
     recall confirmed immediately.
- **Verified end to end, live, with real Anthropic calls (Haiku)**:
  - Long-term memory **persists across sessions for the same user**: told it to
    remember a portfolio position + mandate limit in one session; a brand-new
    session (no shared header) for the same `user` correctly recalled both via
    `search_memory`, and correctly added the honest caveat that checking the
    position against the limit needs Portfolio Risk Lead (not built yet).
  - Long-term memory **does not leak across users** (tested at the store level,
    `test_search_scoped_to_user`).
  - Short-term session continuity: told it "my lucky number is 42, don't save it,"
    then in the SAME `X-Session-Id`, asked "what did I just say" — answered "42"
    correctly, from restored conversation history alone (no memory tool called).
    Confirmed directly in Postgres afterward: "42" was never written to
    `user_memory` — it correctly honored "don't save it."
- New tests: `data/tests/test_memory_store.py` (5 tests, including the exact
  natural-language-query regression above) and `data/tests/test_session_repository.py`
  (5 tests, including a real `RepositorySessionManager` restore-on-a-fresh-object
  check). All 69 `data`/`analysis` tests pass.

**Follow-up (same day)**: user then asked for predefined categories from us,
alongside the user-configurable free text — "predefined from us but user still can
manage their own preference." Discovered while implementing that Strands' generic
`add_memory` tool (built by `MemoryManager`) only exposes `entries: list[str]` to
the model, with no per-entry metadata parameter, so the model itself cannot set a
category. Implemented as server-side auto-classification instead:
`data/memory_store.py::_classify()` tags every fact with one of four predefined
`kind`s from the business doc (`portfolio`, `mandate_limit`, `thesis`,
`preference`) by keyword, defaulting to `other` — purely organizational metadata
for future filtering/analytics (`metadata->>'kind'`), never gating what gets
written or recalled. A caller-supplied `metadata={"kind": ...}` still overrides the
auto-tag. 2 new tests (`test_add_auto_tags_kind_in_metadata`,
`test_add_caller_metadata_overrides_auto_classified_kind`) plus a pure-function
`test_classify_predefined_kinds`. All 72 `data`/`analysis` tests pass.

**Not done**: no UI decided yet, so there's no confirmed source for a session id
other than the gateway's own `X-Session-Id` header scheme built here — a real chat
UI's own conversation-id convention, once one is chosen, may fit more naturally than
asking the UI to echo a header.

## Item 17: Portfolio Risk Lead — third role wired in (2026-09-24)

`gateway/roles/portfolio_risk.py` (new): third specialist, attached to the Chief as
an agent-as-tool exactly like Investment Research Lead (same `preserve_context=False`
boundary — no session/memory of its own). Reuses the existing
`analyze_portfolio`/`analyze_liquidity`/`analyze_returns` tools
(`gateway/tools/portfolio_analysis.py`), which were already wired to real ingested
Postgres data in an earlier session but had no role consuming them yet — the Chief
previously withheld them entirely rather than expose them through a role boundary
they didn't belong to (see the old orchestrator.py docstring, now superseded).

Covers, per the business doc: exposure/weight/concentration (HHI, effective number
of holdings) and single-position liquidity (ADV20, normal-conditions exit days).
Explicitly does NOT cover (system prompt says so rather than fabricating it):
covariance between holdings, portfolio-level volatility/VaR, stress-test/scenario
analysis, benchmark comparison, or checking a weight against the user's actual
mandate limits — this role has no access to the Chief's memory, so mandate-limit
comparison is the Chief's job (pairing a recalled limit from `search_memory` against
this role's exposure numbers), not this role's.

`gateway/roles/orchestrator.py`: updated to list both specialists, updated the
memory/rules sections accordingly, and wired `portfolio_risk_lead.as_tool(...)` in
next to `investment_research_lead.as_tool(...)`. Zero Sectors API credit cost added —
`analyze_portfolio`/`analyze_liquidity`/`analyze_returns` only read already-ingested
Postgres data, never call `SectorsClient` (see data/analysis_bridge.py).

Verified structurally (no LLM call, avoiding spend): built a real `Agent` via
`build_agent()` against real Postgres/Valkey and confirmed `agent.tool_names ==
['investment_research_lead', 'portfolio_risk_lead', 'search_memory', 'add_memory']`.
72/72 existing tests still pass (no test changes needed — the new role has no I/O
of its own beyond tools already covered by `data/tests/test_analysis_bridge.py`).

**Live-tested (2026-09-24, Haiku, 2 real conversations, real Postgres data — BBCA
only, 21 ingested sessions)**:
1. "I hold 500 shares of BBCA and have 10,000,000 IDR cash. What is my
   concentration risk and can I exit normally?" — Chief correctly delegated to
   `portfolio_risk_lead`, which called `analyze_portfolio` then `analyze_liquidity`
   in sequence and reported real numbers (24.0% weight, ADV20 ≈ IDR 723B, exit
   time ≈ instant at 10% participation). Correctly separated "concentration risk"
   from "liquidity risk" as different problems, matching the system prompt's rule,
   and ended by asking for the user's actual mandate limit rather than assuming one.
   **Real finding, not a bug in this session's work**: `analyze_portfolio`'s HHI/
   effective-number-of-holdings is computed only over priced positions' weights,
   which don't sum to 1 when cash is present — with a single 24%-weight holding
   this produces a HHI (0.0574) and effective-holdings (~17.4) that read like a
   diversified book. The model itself caveated this ("though we only see one
   position in this query") and didn't let it drive the bottom line, but the
   underlying `analysis/portfolio.py` metric is misleading on a thin portfolio.
   Pre-existing from an earlier session, not introduced here — flagged, not fixed.
2. "If the rupiah depreciates 10%, how much would my BBCA position lose, and does
   my portfolio breach my mandate?" — correctly called `search_memory` first (found
   nothing, since this session's test user never stored anything), then declined
   the stress-test and mandate-breach parts explicitly ("this system does not yet
   have stress-test/scenario analysis capability"), and asked for positions/mandate
   limits rather than guessing at either. No fabricated numbers.

## Item 18: Market and Event Intelligence Lead + Independent Risk and Evidence
Officer — all five business-doc roles now wired in (2026-09-24)

User asked to "make all the agents" after Portfolio Risk Lead. Built the remaining
two.

**Market and Event Intelligence Lead** (`gateway/roles/market_intelligence.py`,
`gateway/tools/market_intelligence.py`, `data/repositories.py` additions): price/
volume moves (reuses `get_price_history`), plus four endpoints not previously
exposed to any agent — `get_corporate_actions`, `get_filings`, `get_news`,
`get_foreign_flow` — and `get_broker_activity` (a repository function that already
existed from an earlier session but had no tool/role consuming it yet). New CACHE
wrapper in `data/repositories.py` with a flat 1-hour TTL for the four event
endpoints — no epoch/change-detector exists for these the way price_epoch/fund_epoch
do for prices and financials, so a flat TTL is a deliberate compromise (documented
in the code) rather than an assumed-correct number. Two honest limits stated in the
system prompt rather than glossed over: no statistical baseline exists for judging a
move "unusual" (no volatility model — the role describes moves in plain terms, never
a fabricated z-score), and the symbol/date filter parameters on these four endpoints
are UNCONFIRMED to actually filter (data/sectors_client.py's own docstring already
flagged this; the role's system prompt tells it to sanity-check a "filtered" result
before trusting it's actually scoped).

**Independent Risk and Evidence Officer** (`gateway/roles/independent_risk_officer.py`):
reviews a draft answer's evidence and calculations rather than originating analysis
itself — has the full toolset (company report, price history, fundamentals/
valuation, portfolio/liquidity/returns, all four market-intelligence tools) so it
can actually reproduce a specialist's number rather than trust it on the strength of
confident phrasing. Returns one of PASS / PASS WITH LIMITATIONS / REVISE / DATA
BLOCKED / HUMAN ESCALATION per the business doc's table. Deliberately NOT called on
every question — it re-runs tool calls plus its own model call on top of whatever a
specialist already did, so the Chief's system prompt invokes it selectively
(material quantitative claims the user might act on financially), documented as a
real trade-off: a question this role would have caught but the Chief didn't route to
it gets no independent review. The business doc's "cannot be overruled by another
agent" requirement is enforced as an explicit Chief-side rule (REVISE/DATA BLOCKED
must change the presented answer; HUMAN ESCALATION must reach the user explicitly)
rather than a technical guarantee — there is no mechanism stopping the Chief from
ignoring this, same limitation any prompt-level rule has.

`gateway/roles/orchestrator.py`: rewritten to list and route to all four
specialists, with the "material claim → call the reviewer" and "reviewer's decision
cannot be softened" rules added explicitly.

Verified structurally only (no LLM calls, zero spend): built the Chief and confirmed
`agent.tool_names == ['investment_research_lead', 'portfolio_risk_lead',
'market_and_event_intelligence_lead', 'independent_risk_and_evidence_officer',
'search_memory', 'add_memory']`; built each new specialist standalone and confirmed
its tool list matches what its system prompt claims. 72/72 existing tests still
pass unchanged.

**Not done / open**:
- The Independent Risk and Evidence Officer has never actually been exercised
  end-to-end (does the Chief actually invoke it when it should, does a REVISE
  verdict actually change the presented answer) — prompt-level design only so far.
- Per-role model tiering (cheap model for the Chief's routine coordination, a more
  capable model reserved for the reviewer or difficult conflicts) is still not
  implemented — all five roles share one model_entry.

## Item 19: Market and Event Intelligence Lead — live-tested, 1 real bug found and
fixed, 1 prompt gap found and fixed (2026-09-24)

User asked to live-test Market and Event Intelligence Lead specifically including
`get_news`, "cheap." Two real findings from 2 live Haiku conversations against real
BBCA data (not from direct probing — from watching what an actual agent conversation
did, then confirmed via the Phoenix trace of the failing tool call):

1. **`get_news(symbol=...)` genuinely 400s**, confirmed via the real error message
   captured in the trace: `Unsupported query parameter(s): symbol. Allowed:
   commodity_type, end, extension, keyword, limit, offset, sector, start,
   sub_sector, symbols, tags.` — the real parameter is `symbols` (plural). This is
   the opposite of what data/sectors_client.py's docstring had warned about (an
   unrecognized param being silently ignored, returning unfiltered results); this
   endpoint hard-errors instead. **Fixed**: `SectorsClient.get_news` now sends
   `symbols=` instead of `symbol=`, verified against the real API — a direct call
   for BBCA returned 20 real, BBCA-specific news items (dividend increases, August
   2026 profit figures, foreign-flow data, analyst targets). The market_intelligence
   Lead's own system prompt had already told the model to treat a `get_news` error
   honestly rather than fabricate news around it — confirmed live: before the fix,
   the model correctly reported "the news endpoint returned an error for
   symbol-filtered queries... I cannot provide symbol-specific news" instead of
   inventing anything.
   Also confirmed as a side effect: `get_filings(symbol=...)` and
   `get_corporate_actions(symbol=...)` DO filter correctly — a live BBCA query
   returned genuinely BBCA-specific insider-filing and AGM/dividend data, not an
   unfiltered dump. Documented both findings in data/sectors_client.py.
2. **Prompt gap, not a data bug**: with `get_news` fixed, a second live call showed
   `market_and_event_intelligence_lead`'s own answer stayed properly hedged ("likely
   linked to," phrased as unconfirmed), but the Chief's own synthesis on top of it
   dropped that hedging — stated "reflects broader risk-off sentiment... rather than
   company-specific issues" and "signaling insider belief in long-term value" as
   settled fact. The specialist's system prompt already has an explicit
   observation-vs-explanation separation rule (business doc: "Does not infer intent
   or causality from trading patterns alone"); the Chief's own prompt had no
   equivalent, so nothing stopped it from tightening a hedge into a claim during
   synthesis. **Fixed**: added a rule to the Chief's SYSTEM_PROMPT
   (gateway/roles/orchestrator.py) to preserve a specialist's hedges rather than
   presenting them as settled, specifically for causal claims built from news/flow/
   insider-filing data. Not re-verified live after this specific edit (prompt-only
   change, structural build still passes) — first real test of the new rule will be
   whatever the next live market-intelligence conversation is.

All 72 tests still pass (no test file changes — this was a live/manual
verification, matching how item 17's Portfolio Risk Lead check was also done).

**Still open**: Independent Risk and Evidence Officer still has zero live testing.
`get_corporate_actions_calendar`/`get_suspensions`/`get_top_brokers_daily`/
`get_foreign_flow_daily`'s filter params remain unconfirmed (only `filings`/
`corporate_actions`/`news` were checked this round). Real Sectors credit cost of
the four event endpoints is still unmeasured — these calls happened but no
before/after credit count was taken; if that number matters, check the Sectors
dashboard directly rather than assuming from this session's notes.

## Item 20: Concurrent-tool-call race on `.as_tool()`-wrapped specialists — found
live, fixed (2026-09-24)

The item 19 re-test (Chief hedging rule) surfaced a second, separate real bug: the
run's very first line of output was a warning — `tool_name=<
market_and_event_intelligence_lead>, tool_use_id=<...> | agent is already
processing a request`.

**Root cause, confirmed by reading Strands' own source** (not guessed):
`.as_tool()` wraps ONE shared `Agent` instance per specialist — built once in
`build_agent()`, reused for every call the Chief makes to it within a conversation.
`strands/agent/agent.py` defaults every `Agent` to a `ConcurrentToolExecutor`
(`strands/tools/executors/concurrent.py`), which runs every tool call requested in
one LLM turn in parallel. `strands/agent/_agent_as_tool.py` guards the wrapped
sub-agent with a non-blocking lock specifically because a concurrent call would
"corrupt an in-flight invocation" (its own comment) — so when the Chief happened to
request two parallel calls to the *same* specialist in one turn, the second one hit
the lock and got back a hard `"error"` tool result instead of data. The model
recovered by retrying in this instance, but nothing guarantees that: an LLM that
doesn't retry would silently drop that piece of data rather than surfacing an
error to the user — a real, if intermittent, correctness risk.

**Fixed**: `gateway/roles/orchestrator.py` now builds the Chief with
`tool_executor=SequentialToolExecutor()` (`strands.tools.executors`), scoped to the
Chief only — its tools include other wrapped Agents, which are not reentrant.
Deliberately NOT applied to any specialist's own `Agent` build: a specialist's own
tools (e.g. market_and_event_intelligence_lead calling `get_news`/`get_filings`/
`get_corporate_actions` together) are plain function tools with no shared-instance
lock, so concurrent execution there is safe and keeps the latency benefit. Trade-off
of the fix: the Chief now never runs two *different* specialists in parallel either
(some added wall-clock latency on questions that touch multiple specialists), but
no added credit cost — the same calls happen either way, and this now also avoids
the wasted failed-then-retried round trip the bug caused.

**Verified**: re-ran the exact conversation that triggered the original warning
("What's going on with BBCA's insider buying and foreign flow lately? Does it mean
anything?") — no warning this time, `agent.tool_executor` confirmed as
`SequentialToolExecutor` via a structural check, and the answer came back complete,
well-hedged, and even better organized than the pre-fix run (an explicit
"Observations vs. Hypotheses" section). 72/72 tests still pass.

## Item 21: Event-data endpoints now persist to Postgres, not just Valkey
(2026-09-24)

User noticed live: a real Sectors API call for news during item 19/20 testing never
showed up in Postgres, unlike every other real data source in this project
(price_daily, broker_activity, user_memory). Asked whether that should change; chose
"persist to Postgres too" over "leave as Valkey-only cache."

**What changed**: `get_corporate_actions`, `get_filings`, `get_news`, and
`get_foreign_flow` in `data/repositories.py` now write to Postgres on every REAL
fetch (a Valkey cache miss only — a cache hit still writes nothing new, so this adds
zero Sectors credit cost). They reuse tables that already existed in
`data/schema.sql` (`corporate_actions`, `filings`, `news_articles`,
`foreign_flow_daily`) and upsert/insert methods that already existed in `data/db.py`
(`upsert_corporate_actions`, `upsert_filings`, `upsert_news_articles`,
`upsert_foreign_flow`) — both were built in an earlier session for a planned bulk
INGEST job that was never finished, and had zero writers until now. Valkey's 1-hour
TTL stays as the "is this still fresh enough to serve" layer; Postgres is now the
durable audit trail — real for the Independent Risk and Evidence Officer's job to
"follow a material claim back to its source" (business doc), which a TTL-expired
Valkey key can no longer do.

Four new row-builder functions (`_corporate_action_rows`, `_filing_rows`,
`_news_rows`, `_foreign_flow_rows`) map each endpoint's real response shape
(confirmed live, not guessed — captured actual BBCA payloads before writing the
mapping) onto each table's row schema:
- Corporate actions: the API nests by type (`{"corporate_actions": {"agm": [...],
  "dividend": [...], "stock_split": [...], ...}}`) — flattened to one row per item,
  with the date field name varying by type (`ex_date` for dividends, `date` for
  splits, `agm_date` for AGMs).
- News: one real article can cover multiple symbols (`"symbols": ["BBCA.JK",
  "CDIA.JK"]`, confirmed live) — stored as one row per covered symbol so a later
  per-symbol query finds it. `extension` (NOT NULL in the schema, a field from an
  unfinished original ingest design with no equivalent in the real response) is set
  to a constant `"idx"`.
- Foreign flow: confirmed (again) this is a market-wide top-N feed, not
  symbol-filterable — every persisted row covers whatever symbols the API chose to
  return for that date, not necessarily the one a caller asked about.

**Verified end to end, live** (each of these forced exactly one real Sectors API
call, deliberately, to prove the write path — not exploratory): corporate actions
for BBCA went from 0 rows to 25 in Postgres after one fetch (dividends, AGMs, the
2021 stock split, all correctly dated); filings went to 10 rows; news_articles to
20 rows; foreign_flow_daily to 20 rows. The four row-builder functions were also
checked directly against the exact real payload shapes captured during this
verification (pure-function checks, no additional API cost). 72/72 existing tests
still pass; structural agent build unaffected.

**Not done**: no automated test file covers the new persistence path (verified
manually/live this round, matching how items 17/19/20 were also verified). No
retention/pruning policy exists for these audit tables — they will grow unbounded
as an append-mostly log; `filings`/`news_articles` have no unique constraint, so a
repeated real fetch of overlapping data (e.g. two different symbol queries that both
surface the same article) will insert duplicate rows rather than deduping. Not a
correctness problem for an audit trail, but worth knowing if row count ever matters.

## Item 22: Portfolio Risk Lead re-verified after items 20/21; Independent Risk and
Evidence Officer live-tested for the first time (2026-09-24)

**Portfolio Risk Lead re-check**: re-ran a concentration+liquidity question
("1000 shares BBCA + 20,000,000 IDR cash... exit at 5% participation") after the
SequentialToolExecutor fix (item 20) and the market-intelligence persistence change
(item 21), to confirm neither broke this role. Clean run, no concurrency warning,
correct numbers (23.95% weight, HHI 0.057, ~15-second exit). Notably better
interpretation than item 17's original test: the model now explicitly self-corrects
the low-blended-HHI-looks-diversified read ("though equity concentration would be
high if you were fully invested") without being told to this time — either a model
variance or the earlier finding sinking in through session context; not something
this session's prompt changes specifically targeted, so treat as encouraging rather
than confirmed-fixed.

**Independent Risk and Evidence Officer — first live test.** Asked a deliberately
material question ("BBCA P/E and ROE, is it reasonably valued, I'm seriously
considering buying, please have this checked"). Full pipeline worked exactly as
designed:
1. Chief routed to investment_research_lead first for the draft valuation.
2. Chief then explicitly called independent_risk_and_evidence_officer before
   presenting anything — the "material claim" rule in its system prompt fired
   correctly on real judgment, not a forced instruction.
3. The reviewer didn't just re-read the draft — it re-ran analyze_fundamentals,
   get_price_history, get_company_report, get_corporate_actions, and
   analyze_returns itself and verified every numeric claim to the decimal (P/E
   13.4x, ROE 20.8%, NIM 5.67%, NPL 1.65%, etc. all confirmed).
4. It found three genuine analytical gaps the draft had glossed over: an
   unsupported "9-10% cost of equity" figure with no disclosed calculation, a
   "low-growth (3.4%)" narrative that omitted net income actually grew 4.9% YoY,
   and no mention of a 38% P/E compression (21.5x -> 13.2x) over two years.
5. Returned **REVISE**. Per the Chief's system prompt rule, it did NOT present the
   original conclusion — it rebuilt the answer around the corrected picture and
   told the user it cannot recommend buy/sell because the call now depends on the
   user's own cost-of-capital assumption, which the research never made explicit.

One non-bug worth noting: the reviewer's own tool calls included one real 400 —
it guessed invalid `get_company_report` section names ("balance sheet", "income
statement", "bank metrics" — not real sections; the confirmed real list is
overview/valuation/future/peers/financials/dividend/management/ownership, see
data/repositories.py). The tool correctly rejected it and the model recovered by
retrying with valid sections on its next call — graceful degradation working as
intended, not a code defect.

Cost: 11 model calls, ~22.7k total tokens for this one conversation (all Haiku) —
no dollar figure given, since no confirmed Haiku $/token rate is documented in this
project to convert against honestly.

**Not done**: this was one conversation, one question shape (a valuation claim).
Whether the Chief reliably calls the reviewer for OTHER kinds of material claims
(a portfolio-risk conclusion, a market-intelligence causal claim) is still
unverified — the "call it selectively" rule is a judgment call by the model each
time, not a hard trigger, so it could just as easily be skipped on a similar-looking
question. Whether the Chief correctly handles a PASS, DATA BLOCKED, or HUMAN
ESCALATION verdict (as opposed to REVISE) is also still unverified — only REVISE
has been observed live so far.

## Item 23: Per-agent token/turn caps + off-topic guard, so a runaway loop or an
off-scope question can't quietly burn credit (2026-09-24)

User asked for two things: a hard limit/guardrail per tool against "leaking" (e.g.
retry loops with no cap), and a way to avoid spending tokens on non-finance
questions outside this system's scope.

**Guardrail 1: per-agent Limits cap.** Strands has a native mechanism for exactly
this — `strands.types.agent.Limits` (`turns`, `output_tokens`, `total_tokens`),
checked at turn boundaries; when tripped, the loop ends gracefully
(`stop_reason="limit_turns"`/`"limit_total_tokens"`, no exception) rather than
erroring or corrupting state. But it's a per-CALL kwarg to `agent(...)`/
`stream_async(...)`, not something settable once on an Agent — and confirmed by
reading `strands/agent/_agent_as_tool.py`: `.as_tool()` calls the wrapped
specialist's `stream_async(prompt, cancel_signal=cancel_signal)` directly, without
forwarding a `limits` kwarg at all. So every specialist in this project (all
wrapped via `.as_tool()` — gateway/roles/orchestrator.py) would have had NO cap of
its own; the Chief's own limits only bound the Chief's loop, and "call a
specialist" counts as a single Chief turn no matter how long that specialist runs
internally.

**Fixed with `gateway/bounded_agent.py`'s `BoundedAgent(Agent)`**: overrides only
`stream_async` to fall back to a stored `default_limits` whenever the caller didn't
pass one explicitly — confirmed via reading `Agent.__call__`/`invoke_async`, both
internally call `self.stream_async(...)`, so this one override transparently covers
every entry point (`agent(...)`, `invoke_async`, `stream_async`, and `.as_tool()`'s
direct call) via normal polymorphism. Never tightens or loosens an explicit
caller-supplied `limits` — only fills the gap when nothing was supplied.

Every role now builds a `BoundedAgent` instead of a plain `Agent`, with a cap sized
generously above what live testing has actually shown (items 17/19/20/22), as a
backstop rather than a normal-use budget:
- investment_research_lead: turns=8, total_tokens=80,000 (observed: 2-3 tool calls)
- portfolio_risk_lead: turns=6, total_tokens=60,000 (observed: 2-4 tool calls)
- market_and_event_intelligence_lead: turns=10, total_tokens=100,000 (observed: 4-5)
- independent_risk_and_evidence_officer: turns=16, total_tokens=180,000 (observed: 6
  tool calls in its first live test — the most tool-heavy role by design)
- Chief: turns=40, total_tokens=400,000 (observed: 11 model calls / ~22.7k tokens
  for one multi-specialist conversation in item 22)

**Verified the cap actually engages at runtime**, not just structurally: built a
throwaway `BoundedAgent` with `turns=1` and a system prompt instructing 3
sequential tool calls — confirmed `stop_reason == "limit_turns"` after exactly 1
turn, both called directly AND through the real `.as_tool()` path (a toy Chief
wrapping the capped specialist) — in the latter case the specialist was cut off
mid-tool-execution, before it could produce a text answer, and the Chief correctly
received and relayed that degraded (but not corrupted or erroring) result. This is
the actual failure mode a real runaway loop would hit: a graceful, bounded stop, not
a crash.

**Guardrail 2: off-topic scope check, prompt-level.** Added a "Scope check, before
anything else" rule at the top of the Chief's SYSTEM_PROMPT (gateway/roles/
orchestrator.py) — a message with no IDX/portfolio/market content gets a direct
1-2 sentence decline with NO specialist or tool call, and a borderline
general-knowledge question (e.g. "what's a P/E ratio") gets answered from the
model's own knowledge rather than spending a tool call to confirm something it
already knows. This is prompt-level, not a technical pre-filter — the Chief still
makes one model call to read any message (unavoidable, since something has to
decide what's in scope), but that's the fixed, small cost every question pays
regardless; the guard prevents the expensive part (specialist delegation, Sectors
API calls) from firing on a question that never needed it.

**Verified live**: asked "Can you write me a poem about the moon?" — the Chief
declined directly in one turn with no tool calls at all (confirmed by the trace: no
`execute_tool`/`invoke_agent` spans, only the Chief's own single `chat` span), for
3,000 total tokens (system prompt + tool schemas — the fixed cost of even
considering the question) versus 20k+ tokens for a real multi-specialist question
in earlier tests.

All 72 existing tests still pass; no test file changes (guardrails verified live/
structurally this round, matching how items 17/19/20/22 were also verified).

**Not done**: no automated test covers `BoundedAgent`'s fallback behavior (verified
manually above). The off-topic guard is a prompt instruction, not enforced in code —
a sufficiently unusual or adversarial phrasing could still talk the model into
calling a specialist for something out of scope; this is a real, inherent limit of
prompt-level guardrails, not a false claim of a hard technical block. The `Limits`
caps are also soft on tokens specifically ("a single oversized model response can
overshoot the budget by one turn" — Strands' own docs), so total_tokens is a
backstop against sustained runaway spend, not a byte-exact ceiling.

## Item 24: Offline compliance-eval dashboard, seeded with real current data
(2026-09-24)

User asked to see the LLM-judge evaluation as a score, clarified via AskUserQuestion
to mean an offline dashboard for the operator/stakeholder (not a live per-answer
badge in the chat product).

**Problem found first**: `evals/phoenix_evals.py`'s `_load_conversations()` only
reads spans named `"chat_completion"` — the name `gateway/telemetry.py::
traced_conversation()` gives a span, only ever created by `gateway/main.py`'s HTTP
handler. Every live-test conversation this session (items 17-22) called
`build_agent()`/`agent(prompt)` directly in throwaway scripts, bypassing that HTTP
layer entirely, so none of them were ever visible to the eval script — the only
existing annotations in Phoenix were 4 stale conversations (including literal
dry-run placeholder answers) from item 11/13, months before this project's current
5-role system existed.

**Fixed by seeding fresh, representative data**, not by changing the eval script:
new `scripts/generate_eval_conversations.py` runs 5 deliberately chosen real
conversations (one per specialist role + one off-topic decline) through the exact
same `traced_conversation()` path `gateway/main.py` uses, so they're indistinguishable
from real product traffic to the eval script. This is a one-time seed script, not
something run on a schedule — it costs real Anthropic credit per run (5 conversations,
including one that deliberately triggers the Independent Risk and Evidence Officer,
the most expensive path in the system) and is documented as such in its own
docstring.

Ran `evals/phoenix_evals.py` against the fresh traces (Haiku judge, 15 new
annotations logged to Phoenix). Real results, not fabricated for the demo:

| Rule | Pass rate | 
|---|---|
| Discloses missing data | 5/5 (100%) |
| No investment recommendation | 4/5 (80%) |
| Cites evidence date | 2/5 (40%) |

**Two real, substantive findings the eval surfaced, not previously caught**:
1. **Systemic**: 3 of 5 conversations cited specific figures (ROE, P/E, NIM, HHI,
   ADV20, etc.) with no fiscal year or as-of date attached, despite every role's
   system prompt explicitly requiring one ("State the fiscal year or as_of date for
   every specific figure you cite" — investment_research.py; similar language
   elsewhere). The rule exists in every prompt; the models don't reliably follow it
   in practice. This is the dashboard's headline finding, not the flashier one below.
2. **Isolated**: the conversation that triggered the Independent Risk and Evidence
   Officer's REVISE verdict (item 22) was graded `gives_recommendation` — the
   Chief's corrected final answer included directive language ("Do not treat the
   13.36x P/E as fairly valued," a numbered "what to do before you buy" list) that
   the judge read as telling the user what to do with their money, not just
   describing findings, despite the standing disclaimer. Ironic given this is the
   ONE conversation that went through independent review — the reviewer caught the
   valuation-logic flaw but didn't catch (because it isn't its job to) that the
   Chief's own corrected framing had drifted into advisory language.

**Built `evals/build_dashboard.py`** (one-off generator, not a recurring job) that
reads the fresh annotations from Phoenix and renders a static HTML report — 3
pass-rate tiles, a "flagged by the judge" callout for the 2 real findings above, and
every conversation's full judge explanations behind a `<details>` disclosure per
rule. Published as an Artifact. Grounded in real data throughout — no lorem, no
fabricated scores; every number traces back to `evals/phoenix_evals.py`'s actual
Haiku-judge output, and the dashboard says exactly that in its own footer
(judge model, conversation count, generation timestamp, and where to audit the
underlying annotations in Phoenix's own trace UI).

**Not done**: this is a static snapshot, not a live-updating dashboard — rerunning
`generate_eval_conversations.py` + `phoenix_evals.py` + `build_dashboard.py` and
republishing is a manual sequence, not automated. 5 conversations is a small,
deliberately cost-bounded sample (one per role), not a statistically meaningful
pass-rate measurement — treat the percentages as a snapshot of this run, not a
long-run quality metric, until a larger, recurring eval run exists.

## Item 25: Arize Phoenix's own agent evaluators (not the custom dashboard) — found
a real bug, one real methodology mistake caught and fixed (2026-09-24)

User clarified after item 24: they meant Phoenix's OWN evaluation UI (already
populated by evals/phoenix_evals.py's `log_span_annotations` calls since item 11),
not a custom-built dashboard — the item 24 artifact was unnecessary; Phoenix's
trace UI already shows every annotation per span. Then asked specifically about
Phoenix's built-in AGENT eval functions — `phoenix.evals.metrics`:
`ToolSelectionEvaluator`, `ToolInvocationEvaluator`, `ToolResponseHandlingEvaluator`
— genuinely different from evals/phoenix_evals.py's hand-rolled text classifiers:
those grade the final answer TEXT against this project's compliance rules; these
grade whether the AGENT USED ITS TOOLS correctly.

**New `evals/agent_evals.py`**, built and run against the same 5 real conversations
from item 24 (no new agent-side LLM/Sectors cost — only new judge calls):
- `ToolSelectionEvaluator` at the Chief level: was the right specialist picked,
  given the real, live-introspected tool list (`agent.tool_registry`, not
  hardcoded — can't drift from gateway/roles/orchestrator.py).
- `ToolInvocationEvaluator` per leaf tool call: were arguments valid against the
  tool's real JSON schema.
- `ToolResponseHandlingEvaluator` per leaf tool call: did the final text correctly
  reflect what the tool actually returned.

**First run was badly flawed, caught before trusting the results**: every leaf
call was fed the plain top-level user question as `input` and the WHOLE
conversation's final answer as `output`, with no real tool schema. Concretely
caught via a raw-trace cross-check: the Independent Risk and Evidence Officer's
legitimate peer-bank comparison (`analyze_fundamentals` for BBCA + BBRI + BMRI +
BBNI + BNLI — real, valuable behavior, see item 22's BBRI-NIM finding) read as
"wrong symbol" errors on every peer call, and nearly every tool's response read as
"hallucinated" because the final answer legitimately synthesizes many OTHER tool
calls' data too, which a single tool's raw result obviously doesn't contain by
itself. User was asked whether to fix and rerun (cost: more Haiku judge credit) or
just document the limitation — chose to fix and rerun.

**Fix**: `input` is now built from the immediate specialist's own task text plus
the other tool calls already made earlier in that same specialist's turn (real
trajectory context, not just the top-level question); `available_tools` uses each
tool's real captured JSON schema (`attributes.gen_ai['tool.json_schema']` on the
span) instead of a bare description. This measurably fixed most invocation
mis-grades (schema-based "hallucinated field" complaints gone; most peer-bank
calls now correctly read as legitimate).

**`ToolResponseHandlingEvaluator` stayed unreliable even after the fix, for a
different, structural reason**: it assumes one tool call maps to one output (its
own docstring examples are exactly that shape). This project's specialists
routinely call 2-4 tools and synthesize ONE answer — comparing any single tool's
raw result against that synthesized text reads as "hallucinated extra data" almost
every time, even when handled correctly, because the "extra" data legitimately
came from the specialist's OTHER tool calls in the same turn. Confirmed by watching
it flag clearly-correct handling as "incorrect" across the board. **Decision: don't
log it** — `evals/agent_evals.py` now excludes `tool_response_handling` from its
default `--metrics`, with the reasoning in the module docstring; opt in explicitly
if you want to see the noise yourself. This is a genuine limitation of that
evaluator for a multi-tool-call-then-synthesize agent design like this one's, not
something fixable by better prompting the judge.

**Real findings from the corrected run** (30 tool_selection + tool_invocation
annotations logged to Phoenix, `idx-agent-gateway` project — visible in its own UI,
no separate dashboard):
1. **A confirmed real bug, previously unnoticed**: the portfolio-risk conversation's
   only `analyze_liquidity` call used `position_value: 0` (verified directly against
   the raw trace event) instead of the actual ~5,040,000 IDR position value (800
   shares x 6,300 IDR close) — producing a trivially-true `normal_exit_days: 0.0`.
   The final answer nonetheless states specific liquidity figures ("5.04 billion
   IDR", "0.7% of daily volume") that match NEITHER that call's result NOR simple
   arithmetic on the real numbers (5.04M / 723B ADV20 ~ 0.0007%, not 0.7% — off by
   roughly 1000x, and "billion" where "million" fits the real position size). Not
   diagnosed further or fixed here — flagged for follow-up, out of this task's scope.
2. **A genuine, debatable tension, not a bug**: even after the trajectory-context
   fix, the judge still sometimes flags a legitimate peer-bank comparison call as
   "incorrect" on strict grounds (the user named BBCA specifically; the tool call
   was for a different symbol). Whether going beyond the literal user request to
   build a more rigorous comparison is good agentic behavior or a scope violation is
   a real, unresolved design question this evaluator surfaces — not something either
   the agent or the eval script is simply getting "wrong."
3. Reconfirms the already-known `get_company_report` invalid-section-guess issue
   from item 22 (the model guessing section names not in the real API's list).

**Not done**: `tool_response_handling` has no reliable evaluation path in this
project yet — would need isolating true per-tool-call intermediate synthesis,
which today's traces don't cleanly capture for a multi-tool-call agent. `evals/
agent_evals.py` has no automated test, same as evals/phoenix_evals.py before it.

## Item 26: `position_value: 0` root-caused and fixed, verified against real trace
AND against Phoenix's own agent evaluator (2026-09-24)

User asked whether item 25's `position_value: 0` finding traced back to incomplete
data. Checked directly against the raw trace: `analyze_portfolio` had already run
and correctly returned `position_values: {"BBCA.JK": 5040000.0}` — not a data gap
at all. The real cause: `portfolio_risk_lead` requested `analyze_portfolio` AND
`analyze_liquidity` in the SAME model turn (both tool_use blocks in one response),
so when the model wrote `analyze_liquidity`'s arguments it had not yet seen
`analyze_portfolio`'s result — it defaulted `position_value` to 0 rather than
waiting. Item 20's `SequentialToolExecutor` fix doesn't touch this: that only
changes execution ORDER after a turn's tool calls are already decided, not WHEN the
model decides each call's arguments — both were already chosen together, before
either ran, regardless of execution order.

**Fixed** in `gateway/roles/portfolio_risk.py`'s SYSTEM_PROMPT: explicit instruction
to call `analyze_portfolio` alone first when a position's dollar value is needed,
read the real `position_values` back, and only then call `analyze_liquidity` with
that number as a separate step — never guess or default a value that should come
from another tool's result. Added as both a specific liquidity-workflow rule and a
general Rules-section principle (applicable to any future tool with a similar
dependency).

**Verified two ways, not just one**:
1. Re-ran the exact conversation that surfaced the bug, traced through the real
   `traced_conversation()` path. Raw trace confirms `analyze_portfolio` and
   `analyze_liquidity` now run in separate `execute_event_loop_cycle`s (separate
   turns), and `analyze_liquidity` was called with `position_value: 5040000` — the
   real number, matching `analyze_portfolio`'s own result exactly.
2. Ran `evals/agent_evals.py`'s real `ToolInvocationEvaluator` against this specific
   conversation, per the user's "and eval for it." First attempt still scored
   `analyze_liquidity` incorrect — not because the fix failed, but because the
   eval script's own trajectory context only included prior calls' ARGUMENTS, not
   their RESULTS, so the judge couldn't verify where 5,040,000 came from and
   reasonably flagged it as unverifiable. Fixed that too (prior-call context now
   includes `tool(args) -> result`, not just `tool(args)`) and reran: both
   `analyze_portfolio` and `analyze_liquidity` scored `correct`, logged to Phoenix.

This is the second time this session a "did the agent's tool use pass eval" check
was itself missing context needed for a fair verdict (item 25's peer-comparison
misread, now this) — worth remembering as a standing lesson for any future
eval-script work in this project: a dependent value's correctness is only
checkable against the PRIOR RESULT that produced it, not the prior call alone.

72/72 tests still pass (prompt-only + eval-script changes, no new test file).

## Item 27: Filling the "uneven test" gaps — 3 new market-intelligence endpoints
wired and live-tested, 4 of 5 Independent Risk Officer verdicts now observed
(2026-09-24)

User asked to fill both gaps flagged earlier: Market Intelligence's never-called
endpoints, and the Independent Risk and Evidence Officer's never-seen verdicts
(only REVISE had been observed, in item 22).

**Market Intelligence: 3 endpoints wired for the first time.**
`get_corporate_actions_calendar` (market-wide, date-windowed — distinct from the
already-wired per-symbol `get_corporate_actions`), `get_suspensions`, and
`get_top_brokers_daily` existed as `SectorsClient` methods but had no repository
function, no gateway tool, and had never once been called against the real API.
Inspected real response shapes live before wiring (not guessed) — `data/
repositories.py` gained 3 new CACHE + Postgres-audit-trail functions (same pattern
as item 21), reusing existing `corporate_actions`/`suspensions`/`broker_rankings`
tables and upsert methods that had sat unused since an earlier, unfinished ingest
design. Added to both `market_and_event_intelligence_lead` and
`independent_risk_and_evidence_officer`'s toolsets (the latter needs them to
reproduce any market-intelligence claim it's reviewing).

**Two real bugs found live while testing the new wiring, both fixed:**
1. The Chief declined a market-wide "what's coming up" question outright, without
   even trying to delegate — because `market_and_event_intelligence_lead`'s short
   `DESCRIPTION` string (the only thing the Chief sees when deciding whether to
   delegate) was never updated to mention the new calendar/suspensions/broker-
   ranking capabilities. Fixed by updating the description; re-tested, now
   delegates correctly.
2. **The model doesn't reliably know today's actual date.** Asked "what's coming
   up in the next couple months," it passed literal `start: "2025-01-01", end:
   "2025-03-31"` to `get_corporate_actions_calendar` — guessing based on training-
   era assumptions rather than the real system date (2026-09-24, confirmed by all
   ingested price data throughout this project). Everything reported as "upcoming"
   was actually 18+ months stale, without looking wrong. Fixed with an explicit
   system-prompt rule: never guess absolute dates for a relative time question —
   omit `start`/`end`/`trade_date` and let the tool's own real-current-date default
   apply, then read the actual dates back from the result. Re-tested: correctly
   omitted the params and reported real, current (Aug-Oct 2026) data.

**A residual concurrency finding, not fully fixed**: the item 20 "already
processing a request" race recurred once during this round's testing. Raw trace
showed `SequentialToolExecutor` DID run the two same-specialist calls back-to-back
(not concurrently — second one started 0.2ms after the first ended) — yet the
second still hit the lock. The model self-recovered via a third retry, same
graceful degradation as before. This looks like a small timing gap between the
wrapped agent's `finally: lock.release()` actually executing and
`SequentialToolExecutor` proceeding to the next tool_use, not a failure of the
item 20 fix's design — but it's a genuine, still-open residual risk in tight-
timing edge cases, not something resolved by that fix alone. Not investigated
further (would require digging into Strands' async-generator/lock internals).

**Independent Risk and Evidence Officer: 3 new verdicts observed live, one
genuine bug found and fixed along the way.**
- **PASS**: asked for BBCA's NIM and cost-to-income ratio with the fiscal year
  stated. The reviewer re-verified every figure against `analyze_fundamentals`
  and `get_company_report` directly, confirmed all three claims, and returned
  PASS — the Chief correctly gated "PASSED INDEPENDENT REVIEW" language on that
  actual verdict, not just because a review was attempted.
- **REVISE (a second real instance, a different failure mode than item 22)**:
  asked about BBCA's FCFE and whether it covers the dividend. `investment_research_lead`
  cited a specific "FCF" figure (609 IDR/share) as if it were a computed FCFE
  proxy, when `analyze_fundamentals`'s own FCFE/FCFF are `Unavailable`. Checked
  directly against `get_company_report`'s raw data: the cited number (75,057,575M
  IDR) is REAL — Sectors' own `historical_financials.free_cash_flow` field,
  confirmed present — so this was NOT fabrication, but an undisclosed metric
  substitution (a different, differently-defined figure presented as if it
  answered the FCFE question). The reviewer caught this along with a real
  305-vs-336 IDR dividend-per-share discrepancy (partial-payments sum vs. the
  AGM's total approved dividend) and a shares-outstanding inconsistency (123.2B
  claimed vs. ~122B implied by market cap), forcing REVISE. **Fixed**:
  `investment_research.py`'s system prompt now requires this substitution be
  named explicitly ("Sectors' reported free_cash_flow", never relabeled as
  "FCFE"/"FCF proxy"), plus a new rule requiring any two conflicting per-share
  figures to be stated with their sources rather than one silently picked. Not
  yet re-verified live (the triggering conversation was expensive — specialist +
  reviewer each made 5-6 tool calls; treated as a straightforward, low-risk prompt
  fix consistent with other verified fixes this session, not worth the repeat
  cost to re-confirm immediately).
- **HUMAN ESCALATION**: asked whether reaching a specific BBCA weight satisfies an
  undefined "reasonable diversification" mandate term. Genuinely a two-turn test
  (the Chief correctly asked for missing portfolio/mandate details before
  attempting anything on the first turn) — and the follow-up turn's question had
  an unintentional logic error of its own (asked about "40% weight" while actually
  describing deploying all remaining cash into BBCA, which is mathematically
  100%, not 40%). The reviewer caught that inconsistency AND correctly identified
  that "reasonable diversification" has no quantitative threshold in the stated
  mandate, returning HUMAN ESCALATION with specific clarifying questions for the
  user. A good validation, if not quite the clean single-turn test originally
  intended.
- **DATA BLOCKED**: not observed this round — the FCFE attempt above produced
  REVISE instead (a defensible, arguably more informative verdict for that
  specific situation: a correctable disclosure problem, not just missing data).
  Not pursued further given cost; 4 of 5 verdicts now confirmed live is treated
  as sufficient coverage for now.

72/72 tests pass throughout. All new live conversations went through the real
`traced_conversation()` path (not bypassing it, unlike some earlier live tests),
so they're visible to both `evals/phoenix_evals.py` and `evals/agent_evals.py` if
graded later.

**Not done**: DATA BLOCKED still unobserved. The FCF-disclosure fix is unverified
live. The concurrency race's exact root cause (async-generator/lock timing inside
Strands) is undiagnosed, only worked around by the model's own retry behavior.
`get_corporate_actions_calendar`/`get_suspensions`/`get_top_brokers_daily`'s
filter params remain as unconfirmed as the other event endpoints were before item
19 — only called with defaults so far, never with an explicit filter tested.

## Item 28: Per-role model tiering — infrastructure wired, deliberately a no-op today

The business doc (section 8) suggests routine coordination should run on modest
reasoning capacity, with more capable models reserved for difficult conflicts.
Asked the user whether to actually activate a stronger model for
`independent_risk_and_evidence_officer` (the natural first candidate — it's the
role whose job is literally to catch what another role got wrong, and the Chief
already calls it selectively rather than on every question, so a pricier model
there doesn't multiply into every request). User chose to keep everything on Haiku
for now and just build the mechanism, given limited Anthropic credit.

What changed:
- `gateway/registry.py`: `select_for_tier(registry, tier, default)` — searches the
  registry for a usable (`supports_tools`, non-placeholder `model_id`) entry of the
  requested tier; falls back to `default` when none exists. `PLACEHOLDER_MODEL_ID =
  "TODO"` made a public constant (was already the convention `models.yaml` used for
  the unfinished `idx-analyst-gpt` entry; now named and reused rather than
  re-typed).
- `gateway/roles/orchestrator.py`: `ROLE_TIERS` dict mapping each of the 5 roles to
  a tier — all `"cheap"` today, with the IRO entry commented as the intended first
  bump to `"strong"`. `build_agent` gained an optional `registry:
  dict[str, ModelEntry] | None = None` param; when given, each role's model_entry
  is resolved via `model_for(role) -> select_for_tier(registry, ROLE_TIERS[role],
  model_entry)` instead of every role sharing the single request-level
  `model_entry` unconditionally. `registry=None` (the default, used by
  `evals/agent_evals.py` and `scripts/generate_eval_conversations.py`, neither of
  which needed changing) preserves the exact old behavior.
- `gateway/main.py`: `_build_agent_with_fallback` now passes `registry=_registry`,
  so the mechanism is actually live in the running gateway — currently a no-op
  since `models.yaml` has no real `strong` model_id yet, verified directly
  (`select_for_tier` resolves `"strong"` back to `idx-analyst-claude` since the new
  `idx-analyst-claude-strong` row's `model_id` is still `TODO`).
- `models.yaml`: added the `idx-analyst-claude-strong` row (tier `strong`, provider
  anthropic, `model_id: TODO`) as a documented, inert placeholder — activating
  tiering later is then a two-line change (a real `model_id` here, `"cheap"` ->
  `"strong"` on one `ROLE_TIERS` entry), not new code.
- `gateway/main.py`'s `/v1/models` listing now excludes any registry entry with a
  placeholder `model_id` (both `idx-analyst-gpt` and the new
  `idx-analyst-claude-strong`) — found while adding the second placeholder that the
  existing `/v1/models` endpoint already exposed the first one as directly
  selectable, which would have errored (`AnthropicModel` given `model_id="TODO"`)
  had anyone picked it from LibreChat's model list. Fixed for both, not just the
  new one.

Verified: `load_registry()` loads the new 3rd row without error; `select_for_tier`
resolves `"cheap"` -> `idx-analyst-claude` and `"strong"` -> falls back to
`idx-analyst-claude` (checked directly, not assumed); `/v1/models`' selectable list
now contains only `idx-analyst-claude`. Full suite: 72/72 still pass (no test
exercised this path directly — gateway/roles has no test file — so this was
runtime-checked by hand, not just by the suite staying green).

**Not done**: no role actually runs on a different model yet — that's exactly what
the user asked to defer. Activating it later needs a real Sonnet `model_id` in
`models.yaml` and a one-line `ROLE_TIERS` change, nothing further.

## Item 29: `admin-ui/` — model-tiering panel, scaffolded against a mock backend

User wants to build their own custom UI (separate from the LibreChat chat UI this
project already sits behind) and asked, for item 28's model tiering specifically,
to build the UI side first rather than a real admin API — so the API can be
designed against a concrete UI instead of guessed at up front.

Scaffolded a real Vite + React + TypeScript app at `admin-ui/` (`npm create
vite@latest admin-ui -- --template react-ts`), not a throwaway mockup. One screen:
a table of the 5 roles, a tier `<select>` per role, and a "Resolves to" column
showing the real model that tier resolves to — mirroring
`gateway/registry.py::select_for_tier`'s fallback logic client-side
(`resolveModelForTier` in `admin-ui/src/api/modelTiers.ts`), so a role set to
`standard` or `strong` today visibly shows "falls back to idx-analyst-claude"
rather than silently implying a model change that wouldn't actually happen. A
reference panel below lists all registered models grouped by tier, placeholders
marked.

Backed by an explicitly-labeled mock (`admin-ui/src/api/modelTiers.ts`), not a real
endpoint — there is no admin API on the gateway yet. `getModelTiering()` /
`updateRoleTier()` are written to the shape the real endpoints are expected to take
(`GET`/`PATCH /admin/model-tiers`) so swapping in real `fetch` calls later touches
only that one file, not the components; state persists to `localStorage` only, so
it survives a page refresh for demo purposes but is per-browser and never reaches
the gateway. `ROLES`/`MODELS` in that file mirror `gateway/roles/orchestrator.py`'s
`ROLE_TIERS` keys and `models.yaml`'s entries by hand — noted in both the module
header and `admin-ui/README.md` as something to keep in sync, or better, replace
with a real endpoint that serves the registry directly.

Verified: `npm run build` (tsc typecheck + vite production build) succeeds clean;
`npm run lint` (oxlint) reports nothing; `npm run dev` starts with no errors in its
log and serves the expected HTML shell. **Not verified**: actual rendered behavior
in a real browser — no browser-automation tool was available in this session to
load the page and interact with it, so "select a tier, see the resolved-model
badge update, refresh and see it persist" was reasoned through from the code, not
watched happen. Worth an actual look before trusting it further.

## Item 30: Docker-bundled `admin-ui`, real Chat + Memory screens, 3 real deployment
bugs found running the full stack for the first time (2026-09-24)

User's ask, in two parts: (1) bundle `admin-ui` into `docker-compose.yml` so it
ships with the rest of the stack, and (2) it's not admin-only — the user will be
showcasing session and memory to others, so it needs real Chat and Memory screens,
not just Model Tiering. Asked two scoping questions first: chat stayed mock (user's
choice, same reasoning as item 29 — nail the UI shape before spending on a real
integration); memory got a real endpoint (user's choice), since there was no way
to show actual remembered facts otherwise.

**Real backend added** — `gateway/main.py`: `GET /v1/memory?user=`, `POST
/v1/memory`, `DELETE /v1/memory/{id}?user=`, all behind the same bearer-auth
dependency as `/v1/chat/completions`. Backed by two `data/db.py` changes:
`search_user_memory` now selects `id` (needed so the UI can address a specific
row to delete it — wasn't previously selected since the Strands `search_memory`
tool path never needed it); `add_user_memory` now returns the inserted row via
`RETURNING` instead of nothing. `delete_user_memory` is new, scoped to `(id,
user_id)` together so one user id can't delete another's memory by guessing ids.
Verified directly against the real local Postgres (add → list → delete → delete-
again-returns-404) before touching the gateway at all, then again through the
gateway's own HTTP endpoints with curl (200s, and a real 401 with no auth header).

**Frontend restructured** — `admin-ui/src/App.tsx` is now a shell with tab
navigation (Chat / Memory / Model Tiering) and a shared `userId` (a plain
localStorage-backed value — there's no real login in this build, matching how
`/v1/chat/completions` already takes a bare `user` field with no auth behind it).
`ModelTiering.tsx` moved under `src/pages/` unchanged in behavior. Two new pages:
`Memory.tsx` (real — list/add/delete against the new endpoints, shows each fact's
auto-classified `kind` tag and timestamp) and `Chat.tsx` (mock — multiple named
sessions with independent persisted history, switching between them, a canned
assistant reply; explicitly trying to reproduce the *user-visible effect* of
`gateway/roles/orchestrator.py`'s real session manager — history surviving a
reload, scoped per session id — using localStorage, not the real mechanism; the
in-UI copy says so). `src/api/client.ts` is a thin same-origin fetch wrapper
(`/api/...`) shared by the one real API module (`memory.ts`) — nothing else
changed shape.

**Docker bundling** — `admin-ui/Dockerfile` (multi-stage: Node build → nginx
serve), `admin-ui/nginx.conf.template`, and a new `admin-ui` service in
`docker-compose.yml` (port 5173). nginx serves the static build AND reverse-
proxies `/api/` to `agent-gateway:8000`, injecting `Authorization: Bearer
${IDX_GATEWAY_KEY}` server-side via the official nginx image's envsubst-on-
templates mechanism — the browser never sees the gateway key and there's no CORS
to configure, since everything is same-origin from the browser's perspective.
`vite.config.ts` gained a matching dev-mode proxy (same `/api` prefix, same
header injection from `admin-ui/.env`'s `IDX_GATEWAY_KEY`) so behavior is
identical in `npm run dev` and in the container.

**This was actually run end-to-end via `docker compose build && up`, not just
written** — and that surfaced three real, previously-undiscovered deployment bugs,
none introduced this session, all now fixed:

1. `gateway/Dockerfile` never `COPY`'d `analysis/` — the gateway container
   crash-looped on import (`ModuleNotFoundError: No module named 'analysis'`)
   the very first time anyone actually built and ran it via Compose. Fixed:
   `COPY analysis analysis` added.
2. `docker-compose.yml` never overrode `POSTGRES_HOST`/`VALKEY_HOST` for
   `agent-gateway` or `ingest-worker` — both default to `localhost` in `.env`
   (correct for this project's actual dev workflow so far, running the gateway
   directly on the host), which inside either container means the container
   itself, not the `postgres`/`valkey` containers. Every DB-backed request
   failed (`psycopg.OperationalError: ... Connection refused`) until fixed by
   overriding both to the in-network service names, same pattern already used
   for `PHOENIX_COLLECTOR_ENDPOINT`.
3. nginx's static `proxy_pass http://agent-gateway:8000/` resolves that hostname
   once at container start and caches it — rebuilding/recreating just the
   `agent-gateway` container (a new internal IP, same name) left the proxy
   pointing at a dead address (`502`, "Host is unreachable") until `admin-ui`
   itself was restarted. Fixed with Docker's embedded DNS resolver
   (`resolver 127.0.0.11 valid=10s`) plus a variable in `proxy_pass`, which
   forces re-resolution per request instead of once. That fix has its own two
   nginx footguns, both hit and fixed in turn: a variable in `proxy_pass` drops
   nginx's automatic "strip the location prefix" behavior for a trailing-slash
   `proxy_pass` (needed an explicit `rewrite ^/api/(.*)$ /$1 break;`), and
   `rewrite ... break` halts the rewrite-phase module for that request — so the
   `set $gateway_upstream ...` line has to come *before* the `rewrite` line, not
   after (the model's first attempt had it after and got "using uninitialized
   ... variable"). Deliberately verified the self-healing worked, not just
   assumed: stopped and recreated the `agent-gateway` container without
   touching `admin-ui`, confirmed a request through the proxy still succeeded.

Verified for real, in this order: `data/db.py` changes directly against local
Postgres; the 3 new HTTP endpoints directly against a locally-run gateway
(`uvicorn`) with curl, including a real 401; the admin-ui dev proxy
(`npm run dev`) forwarding to that same local gateway with no manual auth header;
`tsc -b` + `vite build` + `oxlint` clean; the full Docker path (browser port 5173
→ nginx → agent-gateway container → Postgres container) for add/list/delete: all
real, not mocked, and all actually executed against running containers, not
reasoned about from the code. Full Python suite: 72/72 still pass after the
`data/db.py` signature changes (no existing test asserted the old return shape).

**Not done**: no actual browser click-through — still no browser-automation tool
in this session, so the UI's *rendering* (as opposed to its underlying API calls,
which were verified for real) is unverified. The Chat screen is still mock by the
user's own choice. `db.init_schema()` still isn't wired into the Compose startup
path (see the existing open item below) — this session's Postgres already had the
schema from prior manual runs, so a truly fresh `docker compose up -d postgres`
would still need that run by hand first.

## Item 31: Real admin login (single-admin session auth) + Model Tiering redesign
(2026-09-24)

Two explicit asks: "production level auth" for admin-ui, and "better UI for
config" (Model Tiering specifically). Scoped both with the user first — auth
came back as single admin login (not multi-user accounts: this panel has one
operator, a real accounts system is bigger and not needed yet), config came back
as a real design/UX pass on the existing Model Tiering page, not new screens.

**The actual gap this closes**: before this, admin-ui had *no login at all*.
nginx injected the shared gateway bearer key into every `/api/` request
regardless of who was looking at the page — anyone who loaded the URL already
had full read/write access to `/v1/memory`. That's the thing "production level
auth" needed to fix, not just adding a password field on top.

**Backend** (`gateway/main.py`): three new endpoints, none behind the existing
`_check_auth` bearer dependency (that authenticates *callers* — admin-ui's own
nginx, LibreChat — not a person, and stays unchanged for `/v1/chat/completions`
and `/v1/models`):
- `POST /v1/auth/login` — `ADMIN_PASSWORD` (plaintext in `.env`, same trust tier
  as `IDX_GATEWAY_KEY`/`ANTHROPIC_API_KEY` already sitting there — not hashed at
  rest, since there's no separate password store to leak in the first place, just
  an env-var compare; `hmac.compare_digest` for timing-safety) checked against
  the submitted password, rate-limited via the *existing*
  `gateway/guardrails.py::enforce_rate_limit` (reused, not reimplemented) at 5/min
  keyed on `"admin-login"`. On success: a random `secrets.token_urlsafe(32)`
  token stored in Valkey (`data/cache.py`'s `Cache` — reused as-is, no new
  storage layer) with a 7-day TTL, set as an httpOnly, SameSite=Strict cookie.
  Not `Secure=True` — nothing in this repo terminates TLS yet, noted in the code
  as the thing to flip once it does.
- `POST /v1/auth/logout` — deletes the Valkey token, clears the cookie.
- `GET /v1/auth/verify` — no dependency, because this endpoint *is* the check:
  401 unless the cookie names a live Valkey token. Used two ways: by nginx's
  `auth_request` (below) on every proxied request, and directly by the frontend
  on load to ask "am I already logged in."

**Infra** (`admin-ui/nginx.conf.template`): added `auth_request /auth/verify`
(nginx's native reverse-proxy auth module, confirmed compiled into the
`nginx:1.27-alpine` image via `nginx -V`) in front of the `/api/` location —
every request now needs a valid session cookie before nginx proxies it anywhere,
enforced at the infra layer, not just hidden behind a client-side route guard
(which alone would do nothing — the API would still be reachable directly).
`/api/v1/auth/login` and `/api/v1/auth/logout` are separate exact-match
locations that bypass the gate (login obviously has to work while logged out).

**Frontend**: `src/pages/Login.tsx` (password form, real error states —
wrong-password vs. rate-limited-429, both distinguished), `src/api/auth.ts`
(login/logout/checkSession against the real endpoints), `App.tsx` now checks
session on load and renders Login instead of the shell until authenticated, plus
a sign-out button in the nav. Also inlined the ponytail-audit's own finding from
earlier this session (`useUserId` had exactly one call site) directly into
`App.tsx` while touching that file anyway, and deleted the dead default
Vite/React template assets the same audit flagged
(`src/assets/{react.svg,vite.svg,hero.png}`, `public/icons.svg`) — nothing
referenced them.

**Model Tiering redesign**: table+`<select>` replaced with a card grid (one
`role-card` per role), a three-way segmented tier control instead of a dropdown,
a colored dot + resolved-model name instead of a plain badge (green = real
model for that tier, amber = falling back to cheap), and a new tier legend strip
explaining what cheap/standard/strong actually mean in this system — none of
that existed before, so a first-time viewer had no way to know what picking
"strong" would actually cost or buy them.

Verified for real, in order: `POST /v1/auth/login` with a wrong password (401)
and the right one (200 + cookie) directly against the Dockerized gateway; 6
rapid wrong-password attempts through the SAME rate limiter code path
`/v1/chat/completions` already uses, confirming it actually blocks (429) even a
correct password once tripped, and un-blocks once the minute window rolls over
(waited for it, not assumed); the full browser-shaped path through nginx —
unauthenticated `/api/v1/memory` (401), login, authenticated
`/api/v1/memory` (200), logout, `/api/v1/memory` again (401) — all through
`localhost:5173`, not the gateway directly; `tsc -b` + `vite build` + `oxlint`
clean after every change. Full Python suite: 72/72 still pass.

**Not done**: still no browser-automation tool this session, so the login
screen's and redesigned cards' actual rendering is unverified — every check
above exercised the real HTTP/cookie behavior, not what it looks like. Password
is compared, not hashed, at rest (a deliberate call for a single-admin panel, not
an oversight — see above); revisit if this ever becomes multi-admin. No "remember
me" vs. session-only distinction — every login gets the same 7-day cookie.

## Item 32: Phoenix trace data had no volume — was already gone, not just newly
at risk (2026-09-24)

User caught this by inspection, not from anything flagged in this log: asked
whether Arize Phoenix's data survives a container restart. It didn't —
`docker-compose.yml`'s `phoenix` service had no `volumes:` entry at all, unlike
`postgres`/`valkey` which both had one from the start. Confirmed live: Phoenix
defaults to SQLite at `~/.phoenix/phoenix.db` with no `PHOENIX_WORKING_DIR`/
`PHOENIX_SQL_DATABASE_URL` set, which resolved to `/root/.phoenix/phoenix.db` —
on the container's own writable layer, gone on any recreate.

**Fixed**: added `phoenix_data:/root/.phoenix` to the service and to the
top-level `volumes:` block — same pattern as the other two, no env var needed
since it's the path Phoenix already defaults to.

**The harder finding, checked before claiming anything was preserved**: rather
than just add the volume and move on, backed up the running container's
`/root/.phoenix` first (`docker cp`) so the fix wouldn't itself cause the very
loss it was meant to prevent. That backup turned out to already be empty —
`SELECT COUNT(*) FROM traces` / `FROM spans` both `0`, and the `idx-agent-gateway`
project referenced throughout items 11-27 above wasn't there at all, only an
empty `default` project. So the real data loss had already happened earlier in
*this same session*, almost certainly when an earlier `docker compose up`
recreated the phoenix container as a side effect of rebuilding other services
(items 29-31's repeated `agent-gateway`/`admin-ui` rebuilds) — silently, since
nothing at the time indicated phoenix itself had been touched. **Every trace and
eval annotation this session logged to Phoenix (items 11 through 27) is gone.**
The volume fix stops it from happening again; it does not recover what's
already lost. Said this plainly rather than letting "I added a volume and
copied the data over" imply a recovery that didn't actually happen.

Verified: the new named volume (`sector_agents_phoenix_data`, matching compose's
project-prefixed naming — confirmed via `docker compose config`) mounts at the
exact path Phoenix reads from; the recreated container starts clean, serves
`http://localhost:6006` (200), and both its REST (`/v1/projects`) and GraphQL
APIs correctly show the (empty, real) `default` project — not silently broken,
just empty. Full Python suite unaffected: 72/72 still pass (this was a
Compose-only change).

**Superseded by item 33 below** — the volume fix here was correct but not the
best available fix; kept this entry for the history of how the gap was found.

## Item 33: Phoenix moved onto the existing Postgres, not just given its own
volume (2026-09-24)

User's question after item 32: "why not the log data being fed to the
postgres?" — right call. A second SQLite file with its own volume is a second
thing to ever worry about backing up; Postgres in this stack already is backed
up (has its own volume, is the durable store for everything else). Checked
whether Phoenix actually supports Postgres natively rather than assuming:
`docker exec sector_agents-phoenix-1 python3.13 -c "import phoenix.config as c; ..."`
confirmed `PHOENIX_SQL_DATABASE_URL` is a real, documented config option and
`asyncpg` is already installed in the image — no new dependency, no custom code,
just pointing an existing feature at existing infrastructure.

**Changed**:
- `postgres-init/01-create-phoenix-db.sql` — `CREATE DATABASE phoenix;`, mounted
  read-only at `/docker-entrypoint-initdb.d/` on the `postgres` service. Only
  runs on a genuinely fresh volume (the official Postgres image's own
  convention) — for THIS session's already-initialized volume, ran the same
  statement by hand once: `docker exec sector_agents-postgres-1 psql -U
  idx_agent -d idx_agent -c "CREATE DATABASE phoenix;"`. A separate database, not
  a schema inside `idx_agent` — keeps Phoenix's ~65 tables (its own Alembic-
  managed schema) fully out of this project's own tables.
- `docker-compose.yml`'s `phoenix` service: added
  `PHOENIX_SQL_DATABASE_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/phoenix`
  (interpolated from the root `.env` — Compose reads that file for `${...}`
  substitution in the YAML itself, separately from `env_file:` loading vars into
  a container's own environment) and `depends_on: postgres`. Kept item 32's
  `phoenix_data` volume mounted too, now just a home for non-relational working-
  dir state (wasm cache, dataset exports) — not the source of truth anymore.

Verified, in order, not assumed: confirmed `PHOENIX_SQL_DATABASE_URL` and
`asyncpg` really exist in the image before writing any config; created the
database; recreated the `phoenix` container and watched its own Alembic
migration log run for real against Postgres, ending "✅ Migrations completed";
confirmed 65 real tables landed in the `phoenix` database via `psql`; sent one
real OTLP span through `http://localhost:6006/v1/traces` with a Python
OpenTelemetry exporter and confirmed the row landed in Postgres
(`SELECT COUNT(*) FROM spans` → 1); then — the actual test that matters —
`docker compose stop phoenix && rm -f phoenix && up -d phoenix` (a full
container removal, not just a restart) and confirmed that span was still
there afterward, and the UI came back up clean. Deleted the test span
afterward so it doesn't sit in Phoenix as a fake trace. Full Python suite:
72/72 still pass (Compose/SQL-only change, no Python code touched).

**Not done**: item 32's data loss (items 11-27's traces and eval annotations)
is still gone — this fix prevents it from happening again, it doesn't recover
anything. `postgres-init/` only helps a genuinely fresh `docker compose up` from
here on; anyone restoring this repo onto a pre-existing Postgres volume from
before this change still needs the one-time `CREATE DATABASE phoenix;` by hand.

## Item 34: Chat mock wasn't actually scoped per user — found answering "how do I
prove the session for different user" (2026-09-24)

User asked how to demonstrate that switching the "User" field actually isolates
data between users. Checked before answering: Memory really is (server-side,
`WHERE user_id = %s` — verified live again just now: added a fact as `alice`,
confirmed `bob` sees an empty list, confirmed `alice` still sees it). Chat's mock
was not — `src/api/chat.ts` kept every session in one shared `localStorage` key
with no `userId` field at all, so switching users did nothing for that tab; every
session showed up for everyone.

Fixed: `ChatSession` gained a `userId` field (stamped by `createSession`),
`listSessions(userId)` now filters by it, `Chat.tsx` passes `userId` through and
re-fetches when it changes. `sendMessage` stays unfiltered by user — a session id
is already a UUID, a second per-user check on top would be redundant. Also had to
restructure `Chat.tsx`'s load effect into a named async function instead of a
bare `setLoading(true)` before the `.then()` — oxlint's `set-state-in-effect`
rule flagged the original synchronous-looking call; `Memory.tsx` already used the
named-async-function shape for the same reason, so this just matches it.

Verified: `tsc -b` + `vite build` + `oxlint` clean; rebuilt and redeployed the
`admin-ui` container; re-ran the exact alice/bob Memory isolation check through
the live Docker stack (add as alice → empty for bob → still there for alice) to
confirm the whole login+proxy+scoping chain still works after the rebuild. Chat's
own per-user isolation wasn't re-checked with a browser (still no
browser-automation tool), but the code path is now structurally identical to
Memory's (filter by the same `userId` prop), which *was* checked live.

Also noticed while debugging a separate "wrong password" report just before
this: `ADMIN_PASSWORD` in `.env` only takes effect on `agent-gateway` at
container *creation* — editing `.env` while a container is already running does
nothing until it's recreated (`docker compose up -d agent-gateway`), since
`env_file` values are baked in once, not re-read live. Not a bug, just worth
documenting since it produced a confusing "I set it but it's still wrong"
report — the actual value in use can always be checked directly:
`docker exec sector_agents-agent-gateway-1 printenv ADMIN_PASSWORD`.

## Item 35: Memory can now be edited, not just added/removed (2026-09-24)

Straightforward addition: `PATCH /v1/memory/{id}` in `gateway/main.py`
(`UpdateMemoryRequest{user, content}`), backed by `data/db.py`'s new
`update_user_memory(user_id, memory_id, content) -> dict | None` — same
`(id, user_id)` scoping as `delete_user_memory` so one user can't edit another's
fact by guessing an id, verified live (update with the wrong `user` → 404).
Content only: `metadata`'s `kind` tag and `created_at` are untouched by an edit —
an edit corrects a fact, it doesn't re-date or reclassify it.

Frontend: `src/api/memory.ts` gained `updateMemory`; `Memory.tsx` gained an
inline edit mode per row (Edit → text input + Save/Cancel in place of the static
content, matching the add form's own input styling) instead of a separate
edit page or modal — the fact list is already the right place to edit one.

Verified for real, same pattern as every other memory endpoint this session:
add → PATCH → confirm the list shows only the updated content → PATCH with a
different `user` than the one who owns it → 404 → cleanup. `tsc -b` + `vite
build` + `oxlint` clean. Rebuilt and redeployed both `agent-gateway` and
`admin-ui`, re-ran the same add/update/delete sequence through the live
Docker path (`localhost:5173` → nginx → gateway → Postgres), not just against
a locally-run gateway. Full Python suite: 72/72 still pass.

## Item 36: Model tiering's standard/strong tiers now hold real Anthropic models
(2026-09-24)

User asked for real models behind item 28's tiers — Haiku for cheap (already
real), Sonnet for standard, "Sonnet at max effort" for strong — and explicitly
invited a recommendation on the strong-tier choice.

**Recommendation given and implemented**: Opus 5 (`claude-opus-5`) for `strong`,
not Sonnet 5 at max effort. `output_config.effort` only deepens thinking within
one model; Opus is a genuinely more capable model — the actual fit for
`ROLE_TIERS`' own "more capable models reserved for difficult conflicts"
language, not "same model, think harder." ~2.5x Sonnet 5's per-token price
($5/$25 vs $2/$10 per 1M), judged acceptable here specifically because `strong`
is only reachable by `independent_risk_and_evidence_officer`, which the Chief
already calls selectively (see its own module docstring), not on every question.
Sonnet-at-max-effort remains a one-line fallback (`model_id` +
`effort: max` on the same yaml entry) if the user wants it cheaper instead.

**Changed**:
- `gateway/registry.py`: `ModelEntry` gained `effort: str | None = None`.
  `build_model()` now passes `params={"output_config": {"effort": entry.effort}}`
  to `AnthropicModel` when set — confirmed via `strands.models.anthropic
  .AnthropicModel`'s own docstring that `params` passes straight through to the
  real Messages API request body, not a Strands-specific concept. Left `None`
  (param omitted entirely) for Haiku 4.5, which errors if `effort` is present at
  all — confirmed via the claude-api skill's model/effort tables, not assumed.
- `models.yaml`: two new real entries — `idx-analyst-claude-sonnet`
  (`claude-sonnet-5`, tier `standard`, no effort override — the model's own
  default applies) and `idx-analyst-claude-opus` (`claude-opus-5`, tier `strong`,
  `effort: max`). The old `idx-analyst-claude-strong` placeholder is now this
  real Opus entry (renamed to match); `idx-analyst-gpt`'s `standard`-tier
  placeholder stays untouched and un-competing (`select_for_tier` skips it on its
  still-TODO `model_id` regardless of order).

Verified for real, not just constructed: `load_registry()` + `select_for_tier`
resolve `cheap`/`standard`/`strong` to Haiku/Sonnet/Opus exactly as intended;
`build_model()` on the Opus entry produces the right `params` dict and omits it
entirely for Haiku; then three live API calls, smallest first — raw `anthropic`
SDK call to Sonnet 5 with `output_config: {"effort": "high"}` (real 200 response,
"OK"), the same to Opus 5 with `effort: "max"` (real 200 response), then the
actual code path this project uses end-to-end
(`build_model()` -> Strands `AnthropicModel.stream()`) against Opus 5 — all three
succeeded, all three kept to `max_tokens` 16-32 to hold cost to a few tokens.
Full Python suite: 72/72 still pass.

**Not done, and asked the user directly rather than assumed**: no role's
`ROLE_TIERS` entry was changed — every role still defaults to `cheap` (Haiku).
The tiers are real and working now, but whether to actually route any role
(most naturally `independent_risk_and_evidence_officer`, onto `strong`) through
paid Sonnet/Opus calls on every relevant question is a recurring-cost decision,
not a one-time wiring cost like the verification calls above — asked the user
which role(s), if any, to flip, rather than deciding it myself.

## Item 37: Model Tiering is now real — admin-ui changes actually control which
model each role runs on (2026-09-24)

User caught the gap directly: the Model Tiering panel's "falls back to..."
badge mirrored real logic, but the panel itself was still item 29's mock —
changing a tier in the UI only touched `localStorage`, never
`gateway/roles/orchestrator.py`'s `ROLE_TIERS`. Asked to build the real thing.

**Made role tiers live-editable, not just resolvable**: `ROLE_TIERS` (a hardcoded
dict) became `DEFAULT_ROLE_TIERS` (the fallback) plus `resolve_role_tiers(db)`,
which merges in overrides from a new Postgres table
(`role_tier_config`, `data/schema.sql`) — read fresh on every `build_agent()`
call, so a change takes effect on the very next request, no redeploy, no cache
to go stale. `data/db.py` gained `get_role_tiers()`/`set_role_tier()` (a plain
upsert). Chose Postgres over Valkey deliberately: Valkey runs with
`allkeys-lru` (correct for a cache, see data/memory_store.py's own docstring on
this exact tradeoff) — this is standing configuration, not something that
should ever get silently evicted under memory pressure.

**Real endpoints** (`gateway/main.py`): `GET /v1/admin/model-tiers` returns
`{config, models}` — `config` from `resolve_role_tiers`, `models` built live
from `_registry` (name/provider/tier/usable) instead of a UI-side hand-copied
list that could drift from `models.yaml`. `PATCH /v1/admin/model-tiers/{role}`
validates the role is one of the five real ones and the tier is one of
`cheap`/`standard`/`strong` (404/400 respectively otherwise), then upserts.
Both behind the same `_check_auth` bearer dependency as every other endpoint.

**Frontend**: `src/api/modelTiers.ts`'s mock (`localStorage`, hand-copied
`MODELS`) is gone — `getModelTiering()`/`updateRoleTier()` now call the real
endpoints. `ROLES` (display labels/descriptions) stays static frontend data on
purpose — the backend only knows role ids, no reason to move display copy
server-side for a 5-item list. `resolveModelForTier` took `models` as a
parameter instead of closing over a module-level constant, since `models` is
now dynamic (fetched, not hardcoded).

**Verified for real, in escalating layers, not assumed at any point**: `data/
db.py`'s new methods directly against local Postgres (set → get → revert);
`db.init_schema()` re-run live (safe — every `CREATE TABLE` in schema.sql uses
`IF NOT EXISTS`, confirmed by counting them, 12/12) to add the new table without
touching the 11 existing ones; the two HTTP endpoints directly (GET, PATCH an
unknown role → 404, PATCH an invalid tier → 400); **the actual point of this
feature** — PATCHed `independent_risk_and_evidence_officer` to `strong` via the
real HTTP endpoint, then called `build_agent()` inside the running
`agent-gateway` container and inspected the built IRO sub-agent's live
`model.get_config()`: `claude-opus-5`, while every other role's model stayed
`claude-haiku-4-5-20251001` — confirmed the PATCH actually changes runtime
behavior, not just a stored value nothing reads. Reverted the test change.
Then the full browser-shaped path through nginx (GET → PATCH → GET-confirms-it
→ revert), not just against the gateway directly. `tsc -b` + `vite build` +
`oxlint` clean. Full Python suite: 72/72 still pass.

**Not done**: still no browser-automation tool, so the Model Tiering page's
actual rendering (segmented control click → save-state badge → resolved-model
badge update) is unverified visually — every check above exercised the real
HTTP/DB/build_agent() behavior underneath it, not what it looks like on screen.

## Item 38: Model Tiering became per-user, not system-wide (2026-09-24)

User's ask, prompted by a question about where Postgres holds "user config":
each user should be able to pick their own model tiering, not one shared
system-wide config. Real architecture change, not a UI tweak — `role_tier_config`
was keyed by `role_id` alone (one row per role, for everyone); it needed a
`user_id` too, the same scoping `user_memory` already uses.

**Schema**: `role_tier_config` now `PRIMARY KEY (user_id, role_id)`. Since
`CREATE TABLE IF NOT EXISTS` doesn't alter an existing table, and this
session's table only held two harmless test rows (both already matching the
default "cheap", added during item 37's verification), dropped and recreated
it directly rather than writing a throwaway migration for zero real data.

**Backend**: `data/db.py`'s `get_role_tiers`/`set_role_tier` both gained a
`user_id` parameter (filtered `WHERE`, added to the upsert's conflict target).
`gateway/roles/orchestrator.py`'s `resolve_role_tiers(db, user_id)` now scopes
the Postgres lookup to that user; `build_agent()` already receives `user_id`
for memory scoping, so wiring it into tiering too was a one-line change at the
call site. `gateway/main.py`: `GET /v1/admin/model-tiers` now takes a `user`
query param (matching `/v1/memory`'s existing pattern exactly), `PATCH
.../model-tiers/{role}` takes `user` in its body alongside `tier`.

**Frontend**: `modelTiers.ts`'s `getModelTiering`/`updateRoleTier` take
`userId`; `ModelTiering.tsx` takes a `userId` prop (re-fetches on change, same
pattern as `Memory.tsx`/`Chat.tsx`) instead of loading once for everyone;
`App.tsx` passes the same shared `userId` state it already threads through to
the other two tabs. Header copy now says whose tiering is being edited.

Verified for real, escalating the same way as item 37: `data/db.py` directly
against Postgres — set alice's IRO to `strong`, bob's chief to `standard`,
confirmed carol (nobody set anything) gets `{}` back, i.e. all defaults;
`build_agent()` inside the running container for two different `user_id`s with
the same role tiered differently — alice's IRO built on `claude-opus-5`, bob's
on `claude-haiku-4-5-20251001`, same code, same role, genuinely different
models; then the actual browser, via Playwright MCP (see below) — set carol's
IRO to `strong` by clicking the real UI, switched the User field to `bob`, and
the page correctly showed bob's IRO still on `cheap` — real per-user isolation,
not just simulated. `tsc -b` + `vite build` + `oxlint` clean. Full Python
suite: 72/72 still pass.

**Also this round — Playwright MCP finally connected and did real visual
verification for the first time this session** (the browser tool gap flagged
in items 29/31/37 is now closed). Confirmed by actually clicking through, not
just inferring from HTTP/DB behavior: the login screen (empty + wrong-password
error state, which correctly cleared the field), Model Tiering's redesigned
card grid + segmented control + save-state + resolved-model badge (both
before and after this item's per-user change), Memory's add/edit/delete cycle
end-to-end in the UI, and Chat's mock session list. One real process note: a
browser tab that already had the SPA loaded does NOT pick up a new
`admin-ui` build just by clicking around — Vite's hashed bundle filenames mean
a stale tab keeps running the old JS until an actual page reload; hit this
directly as a false-looking 422 (old bundle calling the tiering endpoint
without the new `user` param) before recognizing it as a stale-tab artifact,
not a bug in the new code.

## Item 39: Chat connected to the real gateway — the last mock in admin-ui is gone
(2026-09-24)

User's ask: wire Chat to the real `/v1/chat/completions`, the thing explicitly
deferred back in item 29 ("mock first"). `src/api/chat.ts` rewritten — sessions
themselves stay a local index (`localStorage`; there's no "list my sessions"
endpoint on the gateway), but `sendMessage` now does a real POST. Each local
session's own `id` doubles as the `X-Session-Id` header — `gateway/main.py`
already treats an unrecognized id as new (seeds full history) and a known one as
continuing (trims server-side to just the newest turn), so the client never
branches on new-vs-continuing, it just always sends its full local transcript
plus the same id every time. `Chat.tsx` gained real error handling (a 429 shows
"rate limited", anything else "could not reach the gateway") — the mock never
needed this, since it never failed.

Verified escalating by cost, cheapest first: a raw curl through the full
`nginx -> gateway -> Chief` path with a 4-word question, confirmed the real
disclaimer came back attached; a second curl reusing the same `X-Session-Id`
with a follow-up question ("what did you just say?") — the model correctly
answered "OK", proving real Valkey-backed session continuity, not just a
successful call; then the actual browser via Playwright — a brand new session,
sent "Reply with exactly one word: OK" through the real UI, watched the real
reply render, sent a follow-up in the same session, watched it correctly
remember its own prior turn. `tsc -b` + `vite build` + `oxlint` clean. Full
Python suite: 72/72 (frontend-only change).

**Found a real, small bug while doing this**: the disclaimer text
(`gateway/guardrails.py`'s `NOT_FINANCIAL_ADVICE_NOTICE`) uses markdown italics
(`_..._`), but the chat bubble renders it as plain text — every real reply now
shows literal underscores instead of italics. Not fixed yet (mock replies never
exercised this, so it was invisible until real text started flowing through).
Flagged for the user rather than silently reaching for a markdown library
un-asked.

## Item 40: Chat gained real step-tracing + live-streamed replies (2026-09-24)

Two user asks in sequence: first, that sending a message gave no feedback (a
real bug — the user's own message didn't render until the whole round trip
finished, since React state only updated after `sendMessage` resolved), then,
once dots were added, "make it like step tracing" — show which specialist the
Chief is actually consulting while waiting, not just an opaque spinner.

Step-tracing needs real intermediate signal, which a non-streaming request
can't give (one blob, at the end, or nothing) — so this meant actually turning
on `stream: true`, not just UI polish. Checked live before writing any code
whether Strands even exposes this: ran a real question through
`agent.stream_async()` directly and found `event["current_tool_use"]["name"]`
identifies which top-level tool the Chief is mid-call on — for this agent,
always one of the 4 specialists or `search_memory`/`add_memory`, since those
are its only tools.

**Backend** (`gateway/main.py`'s `_stream_response`): now yields a `step` field
in the SSE delta (alongside the existing OpenAI-standard `content` field)
whenever `current_tool_use.name` changes — deduped, not on every repeated delta
of the same in-progress call. Deliberately stops at the top level: a
specialist's OWN internal tool calls (e.g. investment_research_lead calling
analyze_fundamentals) arrive nested inside a `tool_stream_event` wrapper and
aren't unpacked — which specialist is being consulted is the meaningful signal
to show a user, not which of Sectors' own endpoints it happens to hit.
Non-standard field on an OpenAI-shape chunk; LibreChat or any other
OpenAI-compatible client just won't recognize `delta.step` and ignores it.

**Frontend**: `src/api/chat.ts`'s `sendMessage` rewritten to POST with
`stream: true` and read the response body as a real SSE stream (manual
`getReader()`/`TextDecoder`, not `EventSource` — needs a custom header and a
POST body, which `EventSource` can't do), taking two optional callbacks:
`onStep` and `onDelta` (the latter fires with the accumulating text on every
content chunk — nearly free once the stream reader loop already exists, and
directly answers the original "I don't know if something's happening"
complaint better than steps alone: the reply now visibly types in). `Chat.tsx`
renders three states while `sending`: no step yet (bouncing dots), a step known
but no text yet (pulsing "Consulting Investment Research Lead…", label text
reused from `modelTiers.ts`'s `ROLES` so it can't drift from the real role
names), or text arriving (renders live in an assistant bubble in place of the
indicator). Also fixed the earlier "message doesn't appear until the reply
does" bug properly in this same pass: an optimistic local update shows the
user's bubble the instant Send is clicked, not after the network round trip.

Verified for real, escalating: a raw `agent.stream_async()` call inspected
directly to confirm `current_tool_use` exists before designing anything around
it; a raw streaming curl through the full `nginx -> gateway` path showing the
real `step` chunk arriving ~7 seconds before any content, then real content
chunks after; then the actual browser via Playwright — sent a real question,
screenshotted mid-flight and caught "Consulting Investment Research Lead…"
rendering live, confirmed the final answer (a real, detailed, correctly-cited
dividend-yield analysis) rendered correctly after. Found and fixed a real CSS
bug in the same pass — the step label was wrapping one word per line in a
tiny box; `white-space: nowrap` fixed it (a `width: max-content` attempt first
didn't fully resolve it, replaced). `tsc -b` + `vite build` + `oxlint` clean.
Full Python suite: 72/72 (backend change was additive to an existing function,
no test file covers `gateway/main.py` directly).

**Not done**: nested specialist-level tool steps (e.g. "Investment Research
Lead is running analyze_fundamentals") stay invisible, a deliberate scope
stop, not an oversight — see above. The markdown-italics rendering gap found
in item 39 is still open (mentioned again since it's now more visible with
live-streaming text).

## Not started / open

1. ~~`db.init_schema()` is never called anywhere~~ — done for real: 11 tables exist in
   the running Postgres container. Still not wired into any Compose startup step
   though (a fresh `docker compose up` from scratch still won't auto-create tables).
2. **`evals/promptfooconfig.yaml`** — still has a single `TODO` placeholder test; needs
   the ~15 real questions (infra doc section 13-14).
3. ~~Sectors API endpoint paths are placeholders~~ — resolved this session (item 7),
   except the two still-unresolved endpoints noted there (revenue segments,
   quarterly-dates universe feed).
4. **`_build_agent_with_fallback`** only retries on `build_agent()` construction
   failure, not on a runtime error mid-stream from the primary model — worth deciding
   if that's good enough for MVP. Not touched this session.
5. ~~Never tested against a real Postgres/Valkey/Sectors API~~ — done this session,
   extensively (item 7). Still not tested: the gateway (`gateway/main.py` and friends)
   against a real LLM provider, and the full Compose stack via `docker compose up`.
6. `docker-compose.yml` still hasn't been exercised as a full stack (`agent-gateway`,
   `ingest-worker`, `phoenix`) via `docker compose up` — only `postgres`/`valkey` have
   been brought up individually this session. No chat UI service is defined in it yet
   — pending the LibreChat vs Open WebUI decision.
7. Confirm whether local changes made after the last commit (including everything from
   this session) have actually been committed — check `git status` on resume; as of
   this checkpoint they have NOT been committed.
8. `data/sectors_client.py`'s filter query parameter names (`date`/`start`/`end`/
   `symbol` on the calendar/filings/news/suspensions/brokers endpoints) are best-guess,
   confirmed only to the extent that the bare path with zero params returns 200 — not
   confirmed to actually filter by date/symbol as intended.
9. ~~`portfolio-intelligence-data-gap-analysis-v1.md` needs an update pass~~ — done,
   item 8.
10. ~~`analysis/fundamentals.py` and the raw-component half of `analysis/valuation.py`
    are still unwired to real data~~ — done, item 9 (1 credit spent, BBCA only; field
    mappings not yet confirmed to generalize to other symbols/sectors).
11. ~~The 5-role agent architecture... has not been started~~ — 2 of 5 roles built,
    item 10 (Chief + Investment Research Lead). Still missing: Portfolio Risk Lead
    (would pick up the orphaned `analyze_portfolio`/`analyze_liquidity`/
    `analyze_returns` tools from item 8), Market and Event Intelligence Lead
    (needs the news/corporate-actions/foreign-flow/broker endpoints — none of them
    wired to a repository function or ingest job yet, only present in
    `sectors_client.py`), and the Independent Risk and Evidence Officer with
    Appendix B's review-decision workflow and materiality scoring (PASS / PASS WITH
    LIMITATIONS / REVISE / DATA BLOCKED / HUMAN ESCALATION) — this last one is
    architecturally different from the other two: it needs to review the *Chief's*
    output, not be called as an ordinary delegated sub-agent, so it likely doesn't
    fit the same `Agent.as_tool()` pattern used for Investment Research Lead.
12. ~~No real LLM has been used against this codebase at all this session~~ — done,
    item 12: one real Haiku call, tracing confirmed with real token counts.
    `idx-analyst-gpt` (OpenAI) still has `model_id: TODO` and no key — untested.
13. ~~`evals/phoenix_evals.py` hasn't been run against any real conversation~~ — done,
    item 13. Found and surfaced a real prompt-following gap: Investment Research
    Lead's answers aren't reliably citing the fiscal year despite the system prompt
    asking for it. Not fixed yet.
14. ~~A question that actually triggers the multi-agent + tool-call path hasn't been
    run~~ — done, item 13, and for zero Sectors credit (BBCA was already fully
    cached). A question needing a symbol that is NOT already cached — the one path
    that would spend real Sectors credit in this multi-agent build — still hasn't
    been tried.
15. Investment Research Lead's system prompt says to state the fiscal year for
    every cited figure, but item 13's eval run showed it doesn't reliably do so.
    Worth tightening the prompt (e.g. an explicit instruction to state the fiscal
    year for every figure in the tool's own output, not left to the answer's
    prose) and re-testing with the same eval script.

## Suggested next step
Everything through item 13 is now verified live: real LLM call, real tracing with
real token counts, real multi-agent tool-call delegation (zero Sectors credit, since
BBCA was cached), and a real eval pass that already surfaced one genuine prompt gap
(fiscal-year citation, item 15). The concrete, small next step is fixing that prompt
gap and re-running the same eval to confirm it's resolved — cheap, and closes the
loop on work already in flight. Bigger next steps, worth checking with the user
before picking one: continue the 5-role build (Portfolio Risk Lead is the natural
next role, tools already exist from item 8), validate item 9's fundamentals field
mappings against a second bank/non-bank issuer, or try a symbol that isn't cached
yet to see the multi-agent path actually spend a Sectors credit (item 14).
