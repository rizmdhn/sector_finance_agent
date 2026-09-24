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
12. No real LLM has been used against this codebase at all this session —
    `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are both unset and `models.yaml` still has
    `model_id: TODO` for both entries. Everything in item 10 is verified structurally
    (agents construct, tools wire up correctly) but not behaviorally.

## Suggested next step
The user picked "calculation engine first" (6), "start wiring real data" (7-9), then
"start building the agent" (10, Chief + Investment Research Lead). The most useful
next step is getting a real model key and `model_id` in so item 10 can actually be
run and observed for the first time — everything about it is currently verified only
structurally. After that, either continue the 5-role build (Portfolio Risk Lead is
the natural next role — its tools already exist from item 8, just unattached) or
spend a little more credit validating item 9's fundamentals field mappings against a
second bank/non-bank issuer before trusting them generally. Worth confirming with the
user which they'd rather do next.
