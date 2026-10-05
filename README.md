# Sector's API-Based Finance Agent

A finance analyst agent for the Indonesia Stock Exchange (IDX).

```
chat UI → OpenAI-compatible FastAPI gateway → Strands Agent → hosted LLMs (Anthropic, OpenAI)
                                                    ↓
                                    data layer (Valkey cache + Postgres) → Sectors API
```

**UI: `admin-ui/`** is a Vite + React + TypeScript panel built for this project. It has
a single-admin login and four screens: Chat, Memory, Model Tiering and Evals. The
gateway speaks the standard OpenAI-compatible API, so other
OpenAI-compatible clients (LibreChat, for example) also work.

---

## Quick start

**Step 1: create your secrets file.** Copy the template:

```bash
cp .env.example .env
```

Set these values and leave the rest as they are:

| Variable | What to put there |
|---|---|
| `SECTORS_API_KEY` | Your Sectors API key |
| `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` | At least one LLM provider key |
| `IDX_GATEWAY_KEY` | Any random string, e.g. `my-secret-key-123` |
| `ADMIN_PASSWORD` | The password for the admin panel login |
| `POSTGRES_PASSWORD` | Any random string. Required: Postgres won't start on a fresh volume without it |

> **About `IDX_GATEWAY_KEY`:** the admin panel and the backend use it to authenticate
> to each other. End users never see it. Set it once in this `.env` file; `docker compose up`
> passes the same file to every service, so both sides always match. You only need to
> copy it by hand if you run the admin panel outside Docker with `npm run dev` (see the
> developer section below).

**Step 2: start everything.**

```bash
docker compose up -d
```

This starts the backend (`localhost:8000`), the admin panel (`localhost:5173`), the
ingest worker and the databases. The first run takes a minute while images build.

**Step 3: create the database tables (optional).** The gateway and the ingest worker
both create missing tables on startup, so this is only needed if you want to do it by
hand:

```bash
docker compose exec agent-gateway python -c "from data.deps import get_db; get_db().init_schema()"
```

**Step 4: open the admin panel.** Go to `http://localhost:5173` and log in with the
`ADMIN_PASSWORD` from step 1.

**Step 5: check that the ticker list loaded (~5 Sectors credits, automatic).** On a
fresh database the ingest worker loads the ticker list at startup, and the admin UI
shows "Setting up your data" until it arrives. To confirm it reached Postgres (costs
no credits):

```bash
docker compose exec ingest-worker python -m ingest.cli status
```

You should see `symbol_master` with about 960 rows and `BBCA.JK valid  True`. If it
shows `0 rows`, the worker log gives the reason, usually a Sectors `429` rate limit.
Run the load again by hand:

```bash
docker compose exec ingest-worker python -m ingest.cli seed
```

`seed` is safe to repeat. Each page it has already paid for is remembered for an hour,
so a retry after a `429` only buys the page that failed. If Sectors is rate limiting
you, wait a minute between attempts.

Prices are not fetched at startup. That was the costly part, about 33 credits per
trading day. They arrive from the daily poll after market close (about 16:00-19:00
WIB). Until then, price-based figures show as unavailable and everything else works.
If you need prices today, run a manual pull (it tries yesterday and today, about 33
credits per day that has data):

```bash
docker compose exec ingest-worker python -c "
from data.deps import get_db, get_cache, get_client
from ingest.jobs import universe_close
universe_close.run(get_db(), get_cache(), get_client(), max_backfill_days=1)
print('seeded')
"
```

> **If the ingest worker was down** (a crash, the host was off, the stack was stopped
> overnight): `universe_close` catches up any trading days it missed the next time it
> runs. It goes back at most 14 calendar days, so a long outage doesn't trigger
> hundreds of API calls on restart. Each missed day costs about 33 credits (see the
> comments in `ingest/jobs/universe_close.py`). For a longer gap, run the pull above
> again; it is safe to repeat. To see the current gap:
> ```bash
> docker compose exec agent-gateway python -c "
> from data.deps import get_db
> from data.canonical import idx_today
> print('latest ingested trade date:', get_db().latest_trade_date())
> print('today (Jakarta):', idx_today())
> "
> ```
> A ticker that has a close price but no volume is a separate, per-symbol gap. The
> bulk close feed never includes volume. Fix it with one 1-credit call:
> ```bash
> docker compose exec agent-gateway python -c "
> from data.deps import get_db, get_client
> from data.repositories import ensure_price_detail
> ensure_price_detail(get_db(), get_client(), 'BBCA')  # use the ticker you need
> "
> ```

This setup is local only and has no HTTPS. Don't expose it to the public internet
as it is. Put a proxy (Caddy, nginx, a cloud load balancer) in front first.

<details>
<summary>Developer workflow: running pieces separately instead of the full stack</summary>

Needs **Python 3.10+** (the code uses `X | Y` union syntax, so the default macOS
`python3` 3.9 fails at import) and Docker for Postgres and Valkey.

```bash
docker compose up -d postgres valkey      # published ports, reachable from the host
python3.14 -m venv .venv                  # any 3.10+ interpreter
.venv/bin/pip install -r data/requirements.txt -r requirements-dev.txt
.venv/bin/python scripts/manage.py init-db
```

To use the CLI from the host instead of inside Compose, set `POSTGRES_HOST=localhost`
and `VALKEY_HOST=localhost` in `.env`. The `.env.example` defaults (`postgres`,
`valkey`) are Docker service names and only resolve inside the Compose network.

Running admin-ui with `cd admin-ui && npm run dev` uses a second env file,
`admin-ui/.env` (copy `admin-ui/.env.example`). Set its `IDX_GATEWAY_KEY` to the same
value as the root `.env` by hand. This is the one case where the two files don't stay
in sync automatically, because Docker Compose isn't providing them.

Run only the gateway:
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
ingest/     APScheduler jobs that fill Postgres and bump cache epochs
analysis/   pure-Python calculation engine (portfolio, liquidity, returns, fundamentals), no LLM, no I/O
evals/      LLM-as-judge and promptfoo eval suites
scripts/    CLI for testing ingest and cache without waiting for the scheduler
admin-ui/   the chat / memory / model tiering / evals panel (Vite + React + TypeScript)
```

`data/` is imported by both `gateway/` and `ingest/`, so canonicalization and cache-key
logic live in one place.

## Agent architecture

`gateway/roles/orchestrator.py::build_agent` builds the **Chief Portfolio Intelligence
Orchestrator** with five roles. Each specialist is attached to the Chief as an
agent-as-tool (Strands' `Agent.as_tool()`). Each specialist's module docstring lists
which of its responsibilities are backed by a tool and which are not, and the system
prompts say the same, so an agent doesn't claim coverage it lacks.

| Role | Tools | Covers | Doesn't cover yet |
|---|---|---|---|
| **Chief Portfolio Intelligence Orchestrator** | none (delegates + memory) | Decides which specialists a question needs, combines their findings, keeps hedges instead of turning them into settled fact | n/a |
| **Investment Research Lead** (`gateway/roles/investment_research.py`) | `get_company_report`, `get_price_history`, `analyze_fundamentals`, `analyze_ownership`, `screen_companies` | Company economics, financial quality, valuation, ownership composition (local/foreign split, free float %), one company at a time | Named major shareholders or a controlling-group mapping (no data source provides this); thesis monitoring against a recorded thesis |
| **Portfolio Risk Lead** (`gateway/roles/portfolio_risk.py`) | `analyze_portfolio`, `analyze_liquidity` (optionally with free-float capacity), `analyze_returns` (Postgres, plus a 1-credit lookup for free-float capacity or for a stale price) | Exposure and concentration, exit liquidity for one position, free-float capacity | Covariance, stress tests, benchmark comparison; no direct access to mandate limits (the Chief pairs those from memory) |
| **Market and Event Intelligence Lead** (`gateway/roles/market_intelligence.py`) | price and volume moves, foreign flow, broker activity, filings, corporate actions, news, `analyze_index` | Descriptive "what moved and why", plus what is inside an index (LQ45, IDX30, KOMPAS100, ...) and how its members moved | No significance test for "unusual"; the `symbol` and `date` filters on filings, news and foreign flow are unconfirmed |
| **Independent Risk and Evidence Officer** (`gateway/roles/independent_risk_officer.py`) | the same data tools as the other three, so it can reproduce a calculation | Reviews a draft answer's evidence and calculations and returns PASS / PASS WITH LIMITATIONS / REVISE / DATA BLOCKED / HUMAN ESCALATION | The most expensive step per question, so the Chief calls it selectively |

Two rules exist only in the Chief's system prompt and are not enforced in code: it
can't say "reviewed" or "approved" unless the Independent Risk and Evidence Officer
returned that decision, and a REVISE or DATA BLOCKED verdict has to change the answer
shown to the user.

**Model tiering, per user.** `models.yaml` holds the model registry (`cheap`, `standard`
and `strong` tiers, with Anthropic and OpenAI entries). Every role starts on `cheap`.
`gateway/roles/orchestrator.py::resolve_role_tiers` applies a per-`(user_id, role_id)`
override from Postgres. The Model Tiering screen writes it, and a user can pick a tier
or a specific model by name. The header comment in `models.yaml` explains each entry.

## Session and memory

Two stores with different lifetimes:

- **Short-term (per-session conversation): Valkey.**
  `data/session_repository.py`'s `ValkeySessionRepository` implements Strands'
  `SessionRepository` with a sliding 4-hour TTL. The gateway returns an `X-Session-Id`
  header on the first message. Send it back on later messages and you only need to send
  the newest message, because the history is restored server-side.
- **Long-term (per-user facts): Postgres.**
  `data/memory_store.py`'s `PostgresUserMemoryStore` gives the Chief its `search_memory`
  and `add_memory` tools. Entries are free-form text with no fixed schema. Full CRUD is
  available at `/v1/memory` and in the Memory screen, scoped per user.

  **Holdings are one saved portfolio, not loose notes.** When you say what you hold, the
  Chief saves it with `set_portfolio`. When you later say you bought or sold, it applies
  `record_trades` to the saved holdings and replaces the record, so the Memory screen
  always shows one current portfolio (older versions are kept as invalid, not deleted).
  Editing that entry's text in the Memory screen doesn't change the numbers; tell the
  agent instead. Summaries and extracted facts deliberately leave out holdings and
  prices, because those go stale.

  **Automatic extraction is opt-in per user** (`PATCH /v1/memory/settings`, or the
  toggle in the Memory screen) and off by default. When off, memory only costs
  anything if it is explicitly used. When on, a background task pulls facts,
  preferences and a summary out of each conversation, and every write passes through an
  LLM-judged ADD / UPDATE / SKIP step instead of a blind insert. It runs on Anthropic
  only, because that provider has no embeddings API.

```bash
python scripts/manage.py init-db   # picks up the user_memory table
```

## Observability and evaluation

The `phoenix` service in `docker-compose.yml` traces every gateway request. No extra
setup is needed, since `gateway/main.py` already calls `setup_telemetry()`. Phoenix
stores its data in Postgres rather than SQLite, so traces survive a container restart.
Conversations appear under the `idx-agent-gateway` project at `http://localhost:6006`.

### Reply trace

Each assistant reply has a **Trace · N credits** chip. It opens a drawer listing the
agents consulted, the tools called, model calls, tokens, time and Sectors credits spent,
with an "Open in Phoenix" link. The data is read back from Phoenix
(`GET /v1/admin/traces/{id}`), so it can take a few seconds to appear after a reply.
Set `PHOENIX_PUBLIC_URL` if your browser doesn't reach Phoenix at
`http://localhost:6006`. Red rows are failed or declined calls.

### Evals

Two eval tools answer different questions:

| Tool | Question | Run from |
|---|---|---|
| `evals/promptfooconfig.yaml` | Do fixed questions against the live gateway work? Catches tool-calling failures | CLI: `promptfoo eval` |
| `evals/phoenix_evals.py` | Did past conversations follow this project's compliance rules (cites a date, no buy/sell advice, discloses missing data)? | CLI, or the **Evals tab**: pick a judge model, run it in the background, cancel any time, check results later |

```bash
pip install -r gateway/requirements.txt -r evals/requirements.txt
export ANTHROPIC_API_KEY=...
python evals/phoenix_evals.py --provider anthropic --model claude-haiku-4-5-20251001
```

Results are written back to Phoenix as span annotations by default. Open
`http://localhost:6006`, choose the `idx-agent-gateway` project, and the trace list
shows a column per rule with each conversation's label and the judge's explanation.
The judge's own LLM calls go to a separate `idx-agent-evals` project so they stay apart
from user conversations.

## Calculation engine (`analysis/`)

Pure Python with no dependencies and no I/O. It covers portfolio value, weights and concentration, returns and drawdown, liquidity,
fundamentals, valuation, flow and broker measures, and scenario aggregation.
Each function returns `analysis.types.UNAVAILABLE` for a missing input or
`analysis.types.NM` for a ratio that has no economic meaning. Neither is ever treated as
zero.

```bash
pip install -r requirements-dev.txt
python -m pytest analysis/tests
```

`data/analysis_bridge.py` is the only place that connects these functions to stored
data:

```bash
python scripts/manage.py analyze-portfolio "BBCA=1000,BMRI=500" --cash=10000000
python scripts/manage.py analyze-liquidity BBCA 50000000000
python scripts/manage.py analyze-returns BBCA 1m
python scripts/manage.py analyze-fundamentals BBCA   # 2 credits the first time, then free until the data changes
```

All four are also agent tools (`gateway/tools/portfolio_analysis.py`).

## Testing the data layer with your own key

`scripts/manage.py` runs ingest jobs and cached reads on demand, without waiting for
the scheduler:

```bash
set -a && source .env && set +a   # load .env into the shell

python scripts/manage.py run-job symbol_master        # builds the symbol master; run this first
python scripts/manage.py run-job universe_close       # loads close prices (no volume or market cap)
python scripts/manage.py backfill-price BBCA          # adds volume and market cap for one symbol
python scripts/manage.py get-report BBCA overview
python scripts/manage.py screen "sector='Financials'" --order-by=-market_cap
python scripts/manage.py get-price-history BBCA 1m
```

`run-job quarterly_dates` raises `NotImplementedError`. No working bulk endpoint was
found for the quarterly-dates change detector; see `ingest/jobs/quarterly_dates.py`.

## Credit cost per ticker

Sectors charges about 1 credit per report *section*, not per call:

| Action | Endpoint | Cost, not cached | Cost, cached |
|---|---|---|---|
| `get-report <symbol> overview` | `company/report/{symbol}` | 1 credit | 0, until the next trading day's close lands |
| `get-report <symbol> valuation` | same, `valuation` section | 1 credit | 0, same as overview |
| `analyze-fundamentals <symbol>` | `overview` + `financials` | **2 credits** | 0, until the symbol's data version changes |
| `get-report` with `future`/`peers`/`dividend`/`management`/`ownership` | same endpoint | 1 credit each | 0, same as financials |
| `backfill-price <symbol>` | `daily/{symbol}/` | 1 credit | n/a, written to Postgres permanently |
| `screen "<where>"` | `companies/` | 1 credit per distinct query | 0 for an identical repeat, until the TTL expires |
| `run-job universe_close` (whole market) | `close/`, paginated | about 33 credits (962 symbols ÷ 30 per page) | shared across every symbol and user; run once a day |
| `analyze-liquidity` / `-returns` | Postgres only | **0** | **0**, never calls the Sectors API |
| `analyze-portfolio` / `analyze_portfolio` tool | Postgres; for a symbol whose stored close is out of date, `daily/{symbol}/` | **0** if prices are current, otherwise 1 credit per stale symbol (the agent asks first) | 0 after that, at most one refresh per symbol per day. The CLI never refreshes |
| `analyze_index` (agent tool, e.g. `lq45`) | none; membership is `symbol_master.indices`, filled by the ticker sweep | **0** | **0**, member prices are a free Postgres read. An existing database needs one sweep to fill it (`python -m ingest.cli seed`, about 5 credits, or the Monday run) |
| `analyze_ownership` (agent tool) | `company/shareholders-composition/{symbol}` | 1 credit | 0, until the symbol's data version changes |
| `analyze_liquidity` with `position_shares` (free-float capacity) | same endpoint, for shares outstanding | 1 credit the first time per symbol | 0 after that; free float % itself is a free weekly Postgres read |

**Bringing one new ticker fully online costs 3 credits, once:** `backfill-price`,
`get-report overview`, and the `financials` pull from `analyze-fundamentals` (plus 1
more if you also want `valuation`). Every analysis after that is free until the
underlying data changes.

<details>
<summary>Known gaps where more caching would help</summary>

- **`ingest/jobs/quarterly_dates.py` is not implemented.** The version bump that should
  invalidate the `financials`, `dividend`, `management` and `ownership` caches when a
  new quarterly report appears never fires. That saves credits but can leave data stale.
- **Valkey has no persistent backstop.** A cache flush or a restart without a volume
  means paying again for every per-section call. A Postgres JSONB mirror would make the
  spend durable.
- **Onboard tickers in batches** instead of one at a time during a conversation. The
  total cost is the same, but it avoids surprise spending mid-analysis.

</details>

### Approval before spending credits

In the admin UI Chat, a call that would spend Sectors credit asks you first. When the
agent is about to make a Sectors call that isn't cached, one card appears in the chat
listing the waiting calls and their estimated cost (calls queued together share the
card). The calls wait until you answer:

- **Allow for this reply** makes these calls and any more this reply needs, up to 10
  credits, then asks again. A broad question (an index, several tickers) needs one
  prompt.
- **Allow for this chat** stops asking for the rest of the conversation.
- **Don't fetch** skips the calls. The agent tells you the data wasn't fetched because
  you declined.

No answer within 120 seconds counts as "no". The **Ask before spending credits**
toggle in the chat header (on by default) turns this off. Only the admin UI uses it
(`X-Approval-Mode: ask`); LibreChat, curl and the ingest worker are unchanged. The
gateway stores the answer, and only an authenticated call can set it, so the agent can't
approve itself by typing "yes" in the chat. The state is held in memory, so this assumes
a single gateway process, which is what `docker compose` runs.

## When something goes wrong

The admin UI explains failures and says what to do next:

- **Can't reach the gateway.** The UI got no answer, so the gateway is stopped or has
  crashed. The screen clears on its own when the gateway is back.
- **Setup screen.** Lists a missing model key, a missing or rejected `SECTORS_API_KEY`, a
  database problem, or the reason the first ticker load failed (for example a rate limit).
- **Banner at the top.** A background job failed, such as the daily price pull, but the
  app still works. A 401 from Sectors can mean either a bad key or a plan that doesn't
  include that endpoint; the message says which.
- **In chat.** Shows a rejected or out-of-credit model key, a model or Sectors rate
  limit, an unreachable provider, or a database or cache outage. **Put my question back**
  restores what you typed.

Only a missing `IDX_GATEWAY_KEY` stops the gateway from starting. The ingest worker
records each job's last result in Valkey (`ingest:status:*`), and the UI reads it from
there.
