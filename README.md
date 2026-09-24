# Sector's API-Based Finance Agent

An IDX (Indonesia Stock Exchange) finance analyst agent: a chat UI → an
OpenAI-compatible FastAPI gateway running a Strands Agent → hosted LLMs (Anthropic,
OpenAI) → a data layer (Valkey cache + Postgres) → the Sectors API.

**UI is not decided yet.** The infra doc's original pick was LibreChat, but that's been
pulled out of `docker-compose.yml` — leaning towards Open WebUI instead. Either way, the
gateway speaks a plain OpenAI-compatible API, so any UI that supports a custom
OpenAI-compatible endpoint works without changes to `gateway/`. `librechat.yaml` is kept
around in case LibreChat comes back into consideration.

Original infra design rationale and diagrams: `idx_agent_infrastructure_diagrams_md.md`
(architecture) and `sectors_idx_ingest_cache_plan_md.md` (per-endpoint cache/ingest
strategy). **The actual product target is now
[portfolio-intelligence-business-requirements-v1.1.md](portfolio-intelligence-business-requirements-v1.1.md)**
— a 5-role multi-agent design (Chief Orchestrator, Investment Research Lead, Portfolio
Risk Lead, Market/Event Intelligence Lead, Independent Risk and Evidence Officer) that
supersedes the original single-agent IDX Analyst MVP. See
[portfolio-intelligence-data-gap-analysis-v1.md](portfolio-intelligence-data-gap-analysis-v1.md)
for what Sectors can and can't support against those requirements, and
[PROGRESS.md](PROGRESS.md) for a detailed status checkpoint of what's implemented,
verified, and still open.

## Repo layout

```
gateway/    FastAPI app, OpenAI-compatible /v1 endpoints, model registry, Strands agent, tools
data/       shared package: canonicalization, Valkey cache, Postgres access, Sectors API client
ingest/     APScheduler jobs that populate Postgres and bump cache epochs
analysis/   pure-Python calculation engine (Appendix A of the business requirements) — no LLM, no I/O
evals/      promptfoo eval suite
scripts/    manual CLI for testing ingest/cache without waiting on the scheduler
```

`data/` is imported by both `gateway/` and `ingest/`, so canonicalization and cache-key
logic exist in one place.

## Requirements

- **Python 3.10+.** The codebase uses `X | Y` union syntax throughout; an old system
  `python3` (e.g. 3.9 on macOS by default) will fail at import time.
- Docker + Docker Compose, for Postgres, Valkey, and (eventually) the full stack.
- A Sectors API key, and at least one of an Anthropic or OpenAI API key.

## Setup

1. Copy `.env.example` to `.env` and fill in real values: `SECTORS_API_KEY`,
   `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, `IDX_GATEWAY_KEY` (any secret string — shared
   between whichever chat UI is used and the gateway). For local CLI use (not the full
   Compose stack), set `POSTGRES_HOST=localhost` and `VALKEY_HOST=localhost` — the
   `.env.example` defaults (`postgres`/`valkey`) are Docker service names that only
   resolve from inside the Compose network.
2. Fill in real model IDs in `models.yaml` — both entries currently have
   `model_id: TODO`.
3. Bring up the data stores with published ports so the host-run CLI can reach them:
   ```
   docker compose up -d postgres valkey
   ```
4. Create a venv with **Python 3.10+** (not an old system `python3`) and install deps:
   ```
   python3.14 -m venv .venv   # or any 3.10+ interpreter
   .venv/bin/pip install -r data/requirements.txt -r requirements-dev.txt
   ```
5. Apply the schema:
   ```
   .venv/bin/python scripts/manage.py init-db
   ```

## Testing the data layer against your own key

`scripts/manage.py` runs ingest jobs and cache-backed reads on demand, without waiting
on the scheduler's cron triggers. All endpoint paths in `data/sectors_client.py` are
verified against the live API (not guessed) — see PROGRESS.md for the discovery notes,
including two important corrections: the screener's `where` is a SQL-like string (e.g.
`"sector='Financials'"`), and the bulk daily-close feed has no volume/market cap
(`backfill-price` fills that in per symbol from a different endpoint).

```
set -a && source .env && set +a   # load .env into the shell

python scripts/manage.py run-job symbol_master        # builds the symbol master; run first
python scripts/manage.py run-job universe_close       # populates close prices (no volume/market_cap)
python scripts/manage.py backfill-price BBCA          # backfills volume/market_cap for one symbol
python scripts/manage.py get-report BBCA overview
python scripts/manage.py screen "sector='Financials'" --order-by=-market_cap
python scripts/manage.py get-price-history BBCA 1m
```

`run-job quarterly_dates` will raise `NotImplementedError` — no working bulk endpoint
was found for the quarterly-dates change detector despite direct probing; see
PROGRESS.md and `ingest/jobs/quarterly_dates.py` for the open design question.

## Calculation engine (`analysis/`)

Pure Python, no dependencies, no I/O — implements Appendix A of the business
requirements (portfolio value/weights/concentration, returns/drawdown, liquidity,
fundamentals, valuation, flow/broker measures, simple scenario aggregation).
Covariance/beta/VaR/ES are deliberately not implemented yet, matching the business
doc's own "Later" priority tier for those.

```
pip install -r requirements-dev.txt
python -m pytest analysis/tests
```

Every function returns `analysis.types.UNAVAILABLE` for a missing input or
`analysis.types.NM` for an economically meaningless ratio (e.g. ROE with negative
equity) — per the business doc, neither should ever be silently treated as zero.

`data/analysis_bridge.py` is the only place that wires these pure functions to real
data — it reads already-ingested Postgres data (never calls the Sectors API, so it
never costs a credit) and feeds it into `analysis/portfolio.py`, `analysis/returns.py`,
and `analysis/liquidity.py`:

```
python scripts/manage.py analyze-portfolio "BBCA=1000,BMRI=500" --cash=10000000
python scripts/manage.py analyze-liquidity BBCA 50000000000
python scripts/manage.py analyze-returns BBCA 1m
```

`data/analysis_bridge.py::fundamentals_snapshot` additionally wires
`analysis/fundamentals.py` and the raw-component side of `analysis/valuation.py`
(P/E, P/B, EV/EBITDA, FCFF/FCFE, bank ratios) to the company report's `financials`
section:

```
python scripts/manage.py analyze-fundamentals BBCA
```

This is CACHE-strategy (like `get-report`): 1 credit per report section on the first
call for a symbol, free on every call after that until the symbol's data changes.
Several bank-specific field mappings (loan-to-deposit's numerator, NIM's denominator)
were chosen by cross-checking against Sectors' own precomputed
`historical_financial_ratio` values for BBCA — see `_provenance` in the function's
output and its docstring. FCFF/FCFE come back `Unavailable` for a bank like BBCA
because Sectors has no change-in-operating-working-capital field, which is the
correct behavior per Appendix A, not a bug. All three `analyze-*` price/portfolio
commands above and this one are exposed as agent tools
(`gateway/tools/portfolio_analysis.py`).

## Credit cost per ticker (cost management)

Sectors bills per API call, roughly 1 credit per report *section* requested (not per
call) — confirmed against a real usage log, not estimated:

| Action | Endpoint | Cost when NOT cached | Cost once cached |
|---|---|---|---|
| `get-report <symbol> overview` | `company/report/{symbol}` | 1 credit | 0, until `price_epoch` bumps (next trading day's close lands) |
| `get-report <symbol> valuation` | same, `valuation` section | 1 credit | 0, same as overview |
| `analyze-fundamentals <symbol>` | `overview` + `financials` sections | **2 credits** | 0, until the symbol's cached version bumps |
| `get-report <symbol>` with any of `future`/`peers`/`dividend`/`management`/`ownership` | same endpoint, that section | 1 credit each | 0, same as financials |
| `backfill-price <symbol>` | `daily/{symbol}/` | 1 credit | N/A — written to Postgres permanently; re-run only to refresh |
| `screen "<where>"` | `companies/` | 1 credit per distinct canonical query | 0 for an identical repeat query, until the epoch/TTL for its field class expires |
| `run-job universe_close` (whole-market daily close) | `close/`, paginated | ~33 credits (962 symbols ÷ 30/page) | shared across every symbol and every user — this is the one call that amortizes, run once/day regardless of how many tickers are analyzed |
| `analyze-portfolio` / `analyze-liquidity` / `analyze-returns` | (Postgres only) | **0** | **0** — never calls the Sectors API; needs `backfill-price` done at least once for volume-dependent liquidity numbers |

**Bringing one brand-new ticker fully online** (price history + report + the whole
calculation engine wired) costs **3 credits, once**: `backfill-price` (1) +
`get-report overview` (1) + `analyze-fundamentals`'s `financials` pull (1) — add +1 if
`valuation` is also wanted for Sectors' own precomputed multiples. Every analysis run
on that ticker after that first pull is free until the underlying data actually
changes (a new trading day's close, or a new filing).

### Where more caching would help

- **`ingest/jobs/quarterly_dates.py` is unimplemented** (see PROGRESS.md) — the
  version bump that should invalidate `financials`/`dividend`/`management`/`ownership`
  caches when a new quarterly report lands never fires today. This is actually
  *good* for cost (those sections stay free forever once fetched) but risks serving
  silently stale fundamentals after a real earnings release. Worth fixing before
  relying on this for anything beyond a demo.
- **Valkey has no persistent backstop.** Every CACHE-strategy payload (company report
  sections, screener results) lives only in Valkey; a cache flush or container
  restart without a volume would force every one of those 1-credit-per-section calls
  to be paid again. Mirroring them into a Postgres JSONB table as a durable fallback
  (checked before falling back to a live call) would make the credit spend durable
  across restarts, not just within a single Valkey uptime window.
- **`free_float/` is not wired to anything yet** — `SectorsClient.get_free_float()`
  exists but no ingest job or repository function calls it, so
  `analysis/liquidity.py::free_float_capacity` has no real data source today. It's a
  REFERENCE-strategy list (whole-market, refreshed weekly), so it would cost a
  small, flat, amortized number of credits regardless of ticker count if ingested.
- **Onboard tickers in batches, not one call per analysis.** Because `backfill-price`
  + `overview` + `financials` are three separate credit-costed calls, adding N new
  tickers to a portfolio one at a time across a chat session pays 3N credits spread
  out; doing it as one deliberate priming pass (e.g. before a session starts) is the
  same total cost but avoids surprise per-message spend during analysis.

## Agent architecture

`gateway/agent.py::build_agent` now builds the **Chief Portfolio Intelligence
Orchestrator** (`gateway/roles/orchestrator.py`), the first piece of the 5-role
architecture in `portfolio-intelligence-business-requirements-v1.1.md` section 4 —
replacing the earlier flat single-agent MVP. Only 2 of the 5 roles exist so far:

- **Chief Portfolio Intelligence Orchestrator** — no tools of its own; delegates to
  specialists and synthesizes their answers.
- **Investment Research Lead** (`gateway/roles/investment_research.py`) — wired to
  the Chief as an agent-as-tool (Strands' `Agent.as_tool()`), with
  `get_company_report`, `get_price_history`, `analyze_fundamentals`, and
  `screen_companies`. Covers company economics, financial quality, and valuation;
  ownership/governance and thesis monitoring are named in its system prompt as not
  backed by real tools yet, so it says so rather than fabricating an answer.

**Not implemented yet: Portfolio Risk Lead, Market and Event Intelligence Lead,
Independent Risk and Evidence Officer.** The Chief's system prompt explicitly tells
it to say so whenever a question would need one of them, rather than presenting an
answer as portfolio-risk-checked or independently reviewed when neither has
happened. One concrete consequence: `analyze_portfolio`/`analyze_liquidity`/
`analyze_returns` (Portfolio Risk Lead's territory per the doc's role table) are
**not** attached to the Chief — they're still exposed as CLI commands and as
importable tools in `gateway/tools/portfolio_analysis.py`, ready for a Portfolio Risk
Lead agent to pick up, but nothing in the live agent graph calls them right now.

Both roles currently share whatever model is selected via `/v1/models` — per-role
model tiering (business doc section 8: cheaper models for routine coordination,
stronger ones for disputed conflicts) is not implemented.

**Tested against a live LLM** — `models.yaml`'s `idx-analyst-claude` entry uses
`claude-haiku-4-5-20251001` (deliberately the cheapest current Claude model, given a
limited credit budget). A real conversation, including one that exercised the full
Chief -> Investment Research Lead -> tool-call path, has been run and traced
end to end — see PROGRESS.md items 12-14. `idx-analyst-gpt` (OpenAI) still has
`model_id: TODO` and no key — untested.

## Session and memory

Two different stores for two different lifetimes, both verified live against real
infrastructure (see PROGRESS.md item 16 for the full transcript, including two real
bugs found and fixed along the way):

- **Short-term (per-session conversation), Valkey**: `data/session_repository.py`'s
  `ValkeySessionRepository` implements Strands' `SessionRepository`, wired via
  `RepositorySessionManager` into the Chief only (Investment Research Lead is
  wrapped with `preserve_context=False`, which Strands forbids combining with a
  session manager). Sliding 4-hour TTL. The gateway issues an `X-Session-Id`
  response header on the first message; echo it back on later ones in the same
  conversation and only your newest message needs sending — history is restored
  server-side. No header, or one Valkey no longer recognizes, falls back to today's
  behavior (send the full array).
- **Long-term (per-user facts), Postgres**: `data/memory_store.py`'s
  `PostgresUserMemoryStore` implements Strands' `MemoryStore`, giving the Chief its
  `search_memory`/`add_memory` tools. Deliberately Postgres, not Valkey — Valkey
  runs with `--maxmemory-policy allkeys-lru`, correct for a cache but wrong for
  memory that must actually persist. Deliberately free-form text (not a fixed
  schema): a fact is whatever the user or the Chief decided was worth remembering
  — a portfolio position, a mandate limit, a preference. Searched via Postgres
  full-text search (OR-of-words, ranked by `ts_rank`), not embeddings — zero
  additional API cost, matching this project's credit-consciousness. No automatic
  background extraction or context injection either, for the same reason: memory
  only costs something when the Chief (or the user) actually asks for it.

```
python scripts/manage.py init-db   # picks up the new user_memory table
```

## Observability and evaluation (Arize Phoenix)

`docker-compose.yml`'s `phoenix` service is wired up and verified working, not just
configured:

```
docker compose up -d phoenix        # or it comes up with the full stack
uvicorn gateway.main:app --reload   # already calls gateway.telemetry.setup_telemetry()
```

Every gateway request is traced automatically, with no further wiring needed:
- Strands' own spans (LLM calls, tool calls, the Chief's delegation to Investment
  Research Lead) export via OpenTelemetry to Phoenix's OTLP endpoint — confirmed
  live: a real conversation's trace shows the exact delegation chain
  (`invoke_agent chief_portfolio_intelligence_orchestrator` ->
  `execute_tool investment_research_lead` -> `invoke_agent investment_research_lead`
  -> `execute_tool analyze_fundamentals`) with real token counts on every LLM call.
- One wrapping span per request (`gateway.telemetry.traced_conversation`), tagged
  with OpenInference's `input.value`/`output.value` — the flat shape
  `evals/phoenix_evals.py` reads, as opposed to Strands' more detailed `gen_ai.*`
  spans.
- Real gateway conversations land under the **`idx-agent-gateway`** Phoenix
  project (`http://localhost:6006`); Phoenix buckets by an
  `openinference.project.name` resource attribute, not `service.name`.

**Evaluation**: `evals/phoenix_evals.py` pulls real traced conversations from
Phoenix and grades them with an LLM judge against this project's own compliance
rules (cites a date for cited figures, never gives a buy/sell/hold recommendation,
discloses missing data/unimplemented roles) rather than generic hallucination
templates. Different tool from `evals/promptfooconfig.yaml` (fixed questions at the
live gateway, catches tool-calling failures) — this one grades conversations that
already happened.

```
pip install -r gateway/requirements.txt -r evals/requirements.txt
export ANTHROPIC_API_KEY=...   # whichever provider judges the answers
python evals/phoenix_evals.py --provider anthropic --model claude-haiku-4-5-20251001
```

Results are written back to Phoenix as span annotations **by default** (pass
`--no-log-annotations` to skip this) — so a non-technical reviewer never needs this
script's terminal output at all: open `http://localhost:6006`, pick the
`idx-agent-gateway` project, and the trace list itself shows a column per
classifier (`cites_evidence_date`, `no_investment_recommendation`,
`discloses_missing_data`) with each conversation's label; clicking into a trace
shows the judge's full explanation for each one. Confirmed live — annotations
logged by a real run were independently re-read back via
`Client().spans.get_span_annotations_dataframe()` and matched what was written.

Judge LLM calls land under a **separate `idx-agent-evals`** Phoenix project (kept
distinct from real user conversations, override with `PHOENIX_PROJECT_NAME`) — this
script calls `setup_telemetry()` itself since it's a standalone process, not part of
the gateway. **This split exists because of a real bug found via a user's own
Anthropic dashboard**: an early run showed only ~8.3k tokens tracked in Phoenix
against ~32k actually billed by Anthropic — the eval script's judge calls were
completely untraced. Fixed by wiring `setup_telemetry()` into the script and adding
`openinference-instrumentation-anthropic`/`-openai` (patches the SDK clients
directly for token-usage capture, since `phoenix.evals`' own spans don't carry it
without that). Re-verified after the fix: every judge call now shows real token
counts in Phoenix. See PROGRESS.md item 14 for the full transcript — worth reading
before assuming any token count Phoenix reports is the complete picture for a new
code path that hasn't been checked against a real provider dashboard yet.

Actual finding from a real eval run, not a placeholder: `cites_evidence_date` graded
one BBCA answer `undated` — Investment Research Lead's system prompt asks for a
fiscal year on every cited figure, but the model didn't reliably include one. Not
fixed yet (PROGRESS.md item 15).

## Running the stack

```
docker compose up -d
```

Brings up the agent gateway (on `localhost:8000`), the ingest worker, Valkey, Postgres,
and Phoenix. No chat UI is included yet — hit the gateway directly with curl/Postman, or
add a UI service (see above) once that's decided. This is a local-only setup with no
TLS termination — if it's ever deployed on a public VM, put a proxy (Caddy, nginx, a
cloud load balancer) in front of whichever UI ends up in front. Not yet exercised
end-to-end in this repo — see PROGRESS.md for what has and hasn't been verified.

## Running the gateway alone

```
pip install -r gateway/requirements.txt -r data/requirements.txt
uvicorn gateway.main:app --reload
```

`GET /v1/models` and `POST /v1/chat/completions` both require an
`Authorization: Bearer <IDX_GATEWAY_KEY>` header — the chat UI, whichever one is
eventually configured, sends this too.
