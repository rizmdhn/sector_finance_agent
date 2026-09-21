# Sector's API-Based Finance Agent

An IDX (Indonesia Stock Exchange) finance analyst agent: a chat UI → an
OpenAI-compatible FastAPI gateway running a Strands Agent → hosted LLMs (Anthropic,
OpenAI) → a data layer (Valkey cache + Postgres) → the Sectors API.

**UI is not decided yet.** The infra doc's original pick was LibreChat, but that's been
pulled out of `docker-compose.yml` — leaning towards Open WebUI instead. Either way, the
gateway speaks a plain OpenAI-compatible API, so any UI that supports a custom
OpenAI-compatible endpoint works without changes to `gateway/`. `librechat.yaml` is kept
around in case LibreChat comes back into consideration.

Full design rationale and diagrams live in the two spec docs at the repo root:
`idx_agent_infrastructure_diagrams_md.md` (architecture) and
`sectors_idx_ingest_cache_plan_md.md` (per-endpoint cache/ingest strategy). See
[PROGRESS.md](PROGRESS.md) for a detailed status checkpoint of what's implemented,
verified, and still open.

## Repo layout

```
gateway/    FastAPI app, OpenAI-compatible /v1 endpoints, model registry, Strands agent, tools
data/       shared package: canonicalization, Valkey cache, Postgres access, Sectors API client
ingest/     APScheduler jobs that populate Postgres and bump cache epochs
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
   between whichever chat UI is used and the gateway), Postgres and Valkey credentials.
2. Fill in real model IDs in `models.yaml` — both entries currently have
   `model_id: TODO`.
3. Bring up the data stores:
   ```
   docker compose up -d postgres valkey
   ```
4. Install the data-layer dependencies locally (for the CLI) or rely on the
   per-service `Dockerfile`s for the full stack:
   ```
   pip install -r data/requirements.txt
   ```

## Testing the data layer against your own key

`scripts/manage.py` runs ingest jobs and cache-backed reads on demand, without waiting
on the scheduler's cron triggers:

```
export $(grep -v '^#' .env | xargs)   # load .env into the shell

python scripts/manage.py init-db                     # applies data/schema.sql
python scripts/manage.py run-job symbol_master        # builds the symbol master; run first
python scripts/manage.py run-job universe_close       # populates the price store
python scripts/manage.py get-report BBCA overview
python scripts/manage.py screen '{"sector": "Banks"}' market_cap
python scripts/manage.py get-price-history BBCA 1m
python scripts/manage.py get-movers gainers 1d
```

`data/sectors_client.py`'s `ENDPOINTS` paths are still placeholders pending
verification against the real Sectors API v2 docs — expect 404s there until they're
corrected for your account.

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
