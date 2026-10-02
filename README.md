# Sector's API-Based Finance Agent

An IDX (Indonesia Stock Exchange) finance analyst agent:

```
chat UI → OpenAI-compatible FastAPI gateway → Strands Agent → hosted LLMs (Anthropic, OpenAI)
                                                    ↓
                                    data layer (Valkey cache + Postgres) → Sectors API
```

**UI: `admin-ui/`** — this project's own Vite + React + TypeScript panel (not a
third-party chat client). Real single-admin login, and screens wired to the real
gateway: Chat, Memory, Model Tiering, Evals. See `admin-ui/README.md`. The gateway
still speaks plain OpenAI-compatible API underneath, so any other OpenAI-compatible
client works unchanged.

**Docs map** — start with this README for setup, then:
- [portfolio-intelligence-business-requirements-v1.1.md](portfolio-intelligence-business-requirements-v1.1.md) — the actual product target: a 5-role multi-agent design.
- [portfolio-intelligence-data-gap-analysis-v1.md](portfolio-intelligence-data-gap-analysis-v1.md) — what Sectors can/can't support against those requirements.
- [PROGRESS.md](PROGRESS.md) — detailed status checkpoint: what's implemented, verified, still open.
- `idx_agent_infrastructure_diagrams_md.md` / `sectors_idx_ingest_cache_plan_md.md` — original infra design and per-endpoint cache/ingest strategy.

---

## Quick start

**Step 1 — create your secrets file.** Copy the template and open it in any text editor:

```bash
cp .env.example .env
```

Fill in these values (everything else can stay as-is):

| Variable | What to put there |
|---|---|
| `SECTORS_API_KEY` | Your Sectors API key |
| `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` | At least one LLM provider key |
| `IDX_GATEWAY_KEY` | Make up any random password-like string, e.g. `my-secret-key-123` |
| `ADMIN_PASSWORD` | The password you'll use to log into the admin panel |
| `POSTGRES_PASSWORD` | Make up any random string, same as above — **required**, not optional: the Postgres container refuses to even start without it on a fresh volume |

> **About `IDX_GATEWAY_KEY`:** this is a shared password the admin panel and the
> backend use to talk to each other — think of it as an internal handshake, not
> something end users ever see or type. You only need to set it **once, in this
> one `.env` file** — `docker compose up` (step 2) automatically hands the same
> file to every service, so the admin panel and the backend always agree on it.
> The only time you'd need to match it by hand in a second place is if you run
> the admin panel outside Docker with `npm run dev` (see the collapsed section
> below) — that's a developer workflow, not the normal path.

**Step 2 — start everything:**

```bash
docker compose up -d
```

This starts the backend (`localhost:8000`), the admin panel (`localhost:5173`),
the data-ingestion worker, and the databases it all depends on. Give it a minute
on first run while it downloads/builds everything.

**Step 3 — one-time database setup**, right after step 2 finishes:

```bash
docker compose exec agent-gateway python -c "from data.deps import get_db; get_db().init_schema()"
```

**Step 4 — open the admin panel:** go to `http://localhost:5173` and log in with
the `ADMIN_PASSWORD` you set in step 1. That's it — chat, memory, model settings,
and evals are all in there.

**Step 5 — check the ticker list landed (automatic, ~5 Sectors credits).** On a
fresh database the ingest worker fetches the ticker list by itself at startup; the
admin UI shows a "Setting up your data" screen until it's in. Confirm it landed in
Postgres — this costs **zero** credits:

```bash
docker compose exec ingest-worker python -m ingest.cli status
```

You want `symbol_master` to show ~960 rows and `BBCA.JK valid  True`. If it shows
`0 rows` (the worker log says why — usually a Sectors `429` rate limit), trigger it
again by hand:

```bash
docker compose exec ingest-worker python -m ingest.cli seed
```

`seed` is safe to repeat: every page it already paid for is remembered for an hour,
so a retry after a `429` only buys the page that failed, never the whole list again.
Wait a minute between attempts if Sectors is rate-limiting you.

Prices are **not** fetched at startup (that was the expensive part, ~33 credits per
trading day). They arrive on their own from the daily post-close poll
(~16:00-19:00 WIB); until then price-based figures show as unavailable and
everything else works. Only run a manual price pull if you need it today (it
tries yesterday and today, ~33 credits per day that has data):

```bash
docker compose exec ingest-worker python -c "
from data.deps import get_db, get_cache, get_client
from ingest.jobs import universe_close
universe_close.run(get_db(), get_cache(), get_client(), max_backfill_days=1)
print('seeded')
"
```

> **If the ingest worker was down for a while** (container crashed, host was off,
> you stopped the stack overnight): `universe_close` automatically catches up any
> trading days it missed the next time it runs — it's not limited to "today" only.
> Capped at 14 calendar days back by design, so a long outage doesn't silently
> trigger hundreds of API calls on restart (each missed day costs its own ~33
> credits — see that job's own comments in `ingest/jobs/universe_close.py` for
> why). If the gap is longer than that, or you just want to double-check what's
> actually in the database, run the same seed command above again — it's safe to
> re-run any time — or check the current gap directly:
> ```bash
> docker compose exec agent-gateway python -c "
> from data.deps import get_db
> from data.canonical import idx_today
> print('latest ingested trade date:', get_db().latest_trade_date())
> print('today (Jakarta):', idx_today())
> "
> ```
> A single ticker missing *volume* specifically (close is there, volume isn't —
> `universe_close`'s bulk feed never includes it) is a different, per-symbol gap —
> fixed with one 1-credit call, not a re-run of the above:
> ```bash
> docker compose exec agent-gateway python -c "
> from data.deps import get_db, get_client
> from data.repositories import ensure_price_detail
> ensure_price_detail(get_db(), get_client(), 'BBCA')  # swap in the real ticker
> "
> ```

This is a local-only setup with no HTTPS — don't expose it on the public internet
as-is; put a proxy (Caddy, nginx, a cloud load balancer) in front of it first.

<details>
<summary>Developer workflow: running pieces individually instead of the full stack</summary>

Needs **Python 3.10+** (the codebase uses `X | Y` union syntax — an old system
`python3`, e.g. 3.9 on macOS by default, fails at import time) and Docker for
Postgres/Valkey.

```bash
docker compose up -d postgres valkey      # published ports, reachable from the host
python3.14 -m venv .venv                  # any 3.10+ interpreter
.venv/bin/pip install -r data/requirements.txt -r requirements-dev.txt
.venv/bin/python scripts/manage.py init-db
```

For host-run CLI use (not the full Compose stack), set `POSTGRES_HOST=localhost`
and `VALKEY_HOST=localhost` in `.env` — the `.env.example` defaults
(`postgres`/`valkey`) are Docker service names that only resolve inside the
Compose network.

Running admin-ui in dev mode (`cd admin-ui && npm run dev`) instead of via Docker
uses a *second*, separate env file — `admin-ui/.env` (copy from
`admin-ui/.env.example`). Its `IDX_GATEWAY_KEY` must be set to the exact same
value as the root `.env`'s, by hand — this is the one case where the two files
don't sync automatically, because Docker Compose isn't the one handing them out.

Run just the gateway:
```bash
pip install -r gateway/requirements.txt -r data/requirements.txt
uvicorn gateway.main:app --reload
```
`GET /v1/models` and `POST /v1/chat/completions` both require an
`Authorization: Bearer <IDX_GATEWAY_KEY>` header.

</details>

---

## Repo layout

```
gateway/    FastAPI app, OpenAI-compatible /v1 endpoints, model registry, Strands agent, tools
data/       shared package: canonicalization, Valkey cache, Postgres access, Sectors API client
ingest/     APScheduler jobs that populate Postgres and bump cache epochs
analysis/   pure-Python calculation engine (Appendix A of the business requirements) — no LLM, no I/O
evals/      LLM-as-judge + promptfoo eval suites
scripts/    manual CLI for testing ingest/cache without waiting on the scheduler
admin-ui/   the chat/memory/model-tiering/evals panel (Vite + React + TypeScript)
```

`data/` is imported by both `gateway/` and `ingest/`, so canonicalization and
cache-key logic exist in one place.

## Agent architecture

`gateway/roles/orchestrator.py::build_agent` builds the **Chief Portfolio
Intelligence Orchestrator** with 5 roles (`portfolio-intelligence-business-requirements-v1.1.md`
section 4). Each specialist is attached as an agent-as-tool (Strands'
`Agent.as_tool()`); each specialist's module docstring states exactly which of its
business-doc responsibilities are backed by a real tool and which are not — the
system prompts say so explicitly rather than fabricating coverage.

| Role | Tools | Covers | Doesn't cover yet |
|---|---|---|---|
| **Chief Portfolio Intelligence Orchestrator** | none (delegates + memory) | Decides which specialist(s) a question needs, synthesizes findings, preserves hedges rather than tightening them into settled fact | — |
| **Investment Research Lead** (`gateway/roles/investment_research.py`) | `get_company_report`, `get_price_history`, `analyze_fundamentals`, `analyze_ownership`, `screen_companies` | Company economics, financial quality, valuation, ownership composition (local/foreign holder split, free float %) for one company at a time | Named major shareholders or a controlling-group mapping (no data source has this — see G1 below); thesis monitoring against a recorded thesis |
| **Portfolio Risk Lead** (`gateway/roles/portfolio_risk.py`) | `analyze_portfolio`, `analyze_liquidity` (optionally with free-float capacity), `analyze_returns` (Postgres-only + one 1-credit-then-free lookup for free-float capacity) | Exposure/concentration, single-position exit liquidity, free-float capacity | Covariance, stress-testing, benchmark comparison; no direct access to mandate limits (Chief pairs those from memory itself) |
| **Market and Event Intelligence Lead** (`gateway/roles/market_intelligence.py`) | price/volume moves, foreign flow, broker activity, filings, corporate actions, news, `analyze_index` | Descriptive "what moved and why", plus what's inside an index (LQ45, IDX30, KOMPAS100, …) and how its members moved | No statistical significance test for "unusual"; `symbol`/`date` filters on filings/news/foreign-flow are unconfirmed |
| **Independent Risk and Evidence Officer** (`gateway/roles/independent_risk_officer.py`) | same data tools as the other three (can reproduce a calculation) | Reviews a draft answer's evidence/calculations → PASS / PASS WITH LIMITATIONS / REVISE / DATA BLOCKED / HUMAN ESCALATION | Most expensive step per question — Chief calls it selectively, not on every question |

Two things enforced only in the Chief's system prompt, not structurally: it cannot
claim "reviewed"/"approved" without the Independent Risk and Evidence Officer
actually having returned that decision, and a REVISE/DATA BLOCKED verdict must
change the presented answer, not be absorbed and ignored.

**Model tiering, per user.** `models.yaml` holds the model registry (`cheap` /
`standard` / `strong` tiers, both Anthropic and OpenAI entries). Every role
defaults to `cheap`; `gateway/roles/orchestrator.py::resolve_role_tiers` overlays
a per-`(user_id, role_id)` override read from Postgres — admin-ui's Model Tiering
screen writes it, and a user can pick a tier *or* a specific model by name. See
`models.yaml`'s own header comment for the reasoning behind each entry.

## Session and memory

Two different stores for two different lifetimes:

- **Short-term (per-session conversation), Valkey.**
  `data/session_repository.py`'s `ValkeySessionRepository` implements Strands'
  `SessionRepository`. Sliding 4-hour TTL. The gateway issues an `X-Session-Id`
  response header on the first message — echo it back and only the newest message
  needs sending; history is restored server-side.
- **Long-term (per-user facts), Postgres.**
  `data/memory_store.py`'s `PostgresUserMemoryStore` gives the Chief its
  `search_memory`/`add_memory` tools. Free-form text, not a fixed schema. Full
  CRUD via `/v1/memory` and admin-ui's Memory screen, scoped per user.

  **AgentCore-style automatic extraction is opt-in per user** (`PATCH
  /v1/memory/settings`, toggle in admin-ui's Memory screen) — off by default. Off:
  memory costs nothing unless explicitly used. On: a background task pulls facts/
  preferences/a summary out of every conversation automatically, and every write
  goes through LLM-judged consolidation (ADD/UPDATE/SKIP) instead of blind insert.
  Runs entirely on Anthropic (no embeddings API available on that provider).

```bash
python scripts/manage.py init-db   # picks up the user_memory table
```

## Observability and evaluation

`docker-compose.yml`'s `phoenix` service traces every gateway request
automatically — no extra wiring needed once `setup_telemetry()` runs (already
called by `gateway/main.py`). Backed by Postgres, not ephemeral SQLite, so traces
survive a container restart. Real conversations land under the
`idx-agent-gateway` project at `http://localhost:6006`.

**Two eval tools, different questions:**

| Tool | Question | Run from |
|---|---|---|
| `evals/promptfooconfig.yaml` | Fixed questions against the live gateway — catches tool-calling failures | CLI, `promptfoo eval` |
| `evals/phoenix_evals.py` | Grades conversations that already happened, against this project's compliance rules (cites a date, no buy/sell advice, discloses missing data) | CLI, or **admin-ui's Evals tab** — pick a judge model, run in the background, cancel anytime, check results later |

```bash
pip install -r gateway/requirements.txt -r evals/requirements.txt
export ANTHROPIC_API_KEY=...
python evals/phoenix_evals.py --provider anthropic --model claude-haiku-4-5-20251001
```

Results are written back to Phoenix as span annotations by default — open
`http://localhost:6006`, pick the `idx-agent-gateway` project, and the trace list
shows a column per rule with each conversation's label and the judge's
explanation. Judge LLM calls themselves land under a separate `idx-agent-evals`
project, kept apart from real user conversations.

## Calculation engine (`analysis/`)

Pure Python, no dependencies, no I/O — implements Appendix A of the business
requirements (portfolio value/weights/concentration, returns/drawdown, liquidity,
fundamentals, valuation, flow/broker measures, scenario aggregation). Every
function returns `analysis.types.UNAVAILABLE` for a missing input or
`analysis.types.NM` for an economically meaningless ratio — neither is ever
silently treated as zero.

```bash
pip install -r requirements-dev.txt
python -m pytest analysis/tests
```

`data/analysis_bridge.py` is the only place wiring these pure functions to real
data:

```bash
python scripts/manage.py analyze-portfolio "BBCA=1000,BMRI=500" --cash=10000000
python scripts/manage.py analyze-liquidity BBCA 50000000000
python scripts/manage.py analyze-returns BBCA 1m
python scripts/manage.py analyze-fundamentals BBCA   # 2 credits first time, then free until data changes
```

All four are also exposed as agent tools (`gateway/tools/portfolio_analysis.py`).

## Testing the data layer against your own key

`scripts/manage.py` runs ingest jobs and cache-backed reads on demand, without
waiting on the scheduler's cron triggers:

```bash
set -a && source .env && set +a   # load .env into the shell

python scripts/manage.py run-job symbol_master        # builds the symbol master; run first
python scripts/manage.py run-job universe_close       # populates close prices (no volume/market_cap)
python scripts/manage.py backfill-price BBCA          # backfills volume/market_cap for one symbol
python scripts/manage.py get-report BBCA overview
python scripts/manage.py screen "sector='Financials'" --order-by=-market_cap
python scripts/manage.py get-price-history BBCA 1m
```

`run-job quarterly_dates` raises `NotImplementedError` — no working bulk endpoint
was found for the quarterly-dates change detector; see `ingest/jobs/quarterly_dates.py`.

## Credit cost per ticker

Sectors bills roughly 1 credit per report *section* requested, not per call:

| Action | Endpoint | Cost, not cached | Cost, cached |
|---|---|---|---|
| `get-report <symbol> overview` | `company/report/{symbol}` | 1 credit | 0, until next trading day's close lands |
| `get-report <symbol> valuation` | same, `valuation` section | 1 credit | 0, same as overview |
| `analyze-fundamentals <symbol>` | `overview` + `financials` | **2 credits** | 0, until the symbol's data version bumps |
| `get-report` with `future`/`peers`/`dividend`/`management`/`ownership` | same endpoint | 1 credit each | 0, same as financials |
| `backfill-price <symbol>` | `daily/{symbol}/` | 1 credit | N/A — written to Postgres permanently |
| `screen "<where>"` | `companies/` | 1 credit per distinct query | 0 for an identical repeat, until TTL expires |
| `run-job universe_close` (whole market) | `close/`, paginated | ~33 credits (962 symbols ÷ 30/page) | shared across every symbol/user — run once/day |
| `analyze-portfolio` / `-liquidity` / `-returns` | Postgres only | **0** | **0** — never calls the Sectors API |
| `analyze_index` (agent tool, e.g. `lq45`) | none — membership is `symbol_master.indices`, filled by the ticker sweep | **0** | **0** — member prices are a free Postgres read; an existing database needs one sweep (`python -m ingest.cli seed`, ~5 credits, or the Monday run) to fill it |
| `analyze_ownership` (agent tool) | `company/shareholders-composition/{symbol}` | 1 credit | 0, until the symbol's data version bumps |
| `analyze_liquidity` with `position_shares` (free-float capacity) | same endpoint, for shares outstanding | 1 credit first time per symbol | 0 after — free float % itself is a free weekly Postgres read |

**Bringing one brand-new ticker fully online costs 3 credits, once:**
`backfill-price` + `get-report overview` + `analyze-fundamentals`'s `financials`
pull (+1 more if `valuation` is also wanted). Every analysis run after that is
free until the underlying data actually changes.

<details>
<summary>Where more caching would help (known gaps)</summary>

- **`ingest/jobs/quarterly_dates.py` is unimplemented** — the version bump that
  should invalidate `financials`/`dividend`/`management`/`ownership` caches on a
  new quarterly report never fires. Good for cost, risks silently stale data.
- **Valkey has no persistent backstop** — a cache flush or restart without a
  volume re-pays every 1-credit-per-section call. A Postgres JSONB mirror would
  make the spend durable across restarts.
- **Onboard tickers in batches**, not one at a time mid-conversation — same total
  cost, but avoids surprise per-message spend during analysis.

</details>

### Approval before spending credits

In the admin-ui Chat, a call that would spend Sectors credit asks you first. When the
agent is about to make a **real** Sectors call (a cache miss — cached answers never ask),
a single card appears in the chat listing the waiting calls and their estimated cost
(calls queued together share one card), and they wait:

- **Allow for this reply** — make these calls and any more this one reply needs, up to 10
  credits; past that it asks again. A broad question (an index, several tickers) is one prompt.
- **Allow for this chat** — stop asking for the rest of this conversation.
- **Don't fetch** — skip it; the agent tells you the data wasn't fetched because you declined.

No answer within 120 seconds counts as "no". The toggle under the message box (**Ask me
before spending Sectors credits**, on by default) turns it off. Only admin-ui opts in
(`X-Approval-Mode: ask`); LibreChat, curl and the ingest worker are unchanged. The answer
is stored by the gateway and set only through an authenticated call, so the agent can't
approve itself by writing "yes" in the chat. It lives in memory, so it assumes one gateway
process (what `docker compose` runs).

---

See [PROGRESS.md](PROGRESS.md) for the detailed, dated verification log behind
every claim above (what was confirmed live against real infrastructure, real
bugs found and fixed, open items).

## Reply trace

Each assistant reply has a **Trace · N credits** chip. It opens a drawer listing the agents consulted, tools called, model calls, tokens, time and Sectors credits spent, with an "Open in Phoenix" link. The data is read back from Phoenix (`GET /v1/admin/traces/{id}`), so it can take a few seconds to appear after a reply. Set `PHOENIX_PUBLIC_URL` if Phoenix isn't at `http://localhost:6006` from your browser. Red rows are failed or declined calls.
