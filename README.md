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

Also exposed as agent tools (`gateway/tools/portfolio_analysis.py`). Not yet wired:
`analysis/fundamentals.py` and the raw-component parts of `analysis/valuation.py`
(P/E from earnings, FCFF/FCFE, bank ratios) — these need the company report's
`financials` section, which has not been fetched for any symbol yet since it costs a
Sectors credit per section; see `portfolio-intelligence-data-gap-analysis-v1.md` (G4).

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
