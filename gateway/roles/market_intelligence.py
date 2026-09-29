"""Market and Event Intelligence Lead: unusual price/volume moves, foreign flow,
broker activity, filings, and corporate events. See
portfolio-intelligence-business-requirements-v1.1.md section 4, "Market and Event
Intelligence Lead".

Real data, but with two honest limits this system prompt states explicitly rather
than papering over:

1. **No statistical baseline for "unusual".** The business doc asks this role to
   "establish [a move's] size, observation window, and comparison baseline" — this
   build can compute the move itself (via get_price_history) but has no historical
   distribution or volatility-adjusted threshold to judge it against. The model
   must describe the move in plain terms (percent change, multiple of a simple
   recent average) rather than asserting a z-score or percentile it cannot compute.
2. **Filter parameters are unconfirmed.** data/sectors_client.py's module docstring
   flags that the `symbol`/`date` query params on corporate-actions/filings/news/
   foreign-flow were only ever tested with zero params — an unrecognized filter is
   typically just ignored by a REST API (200, unfiltered results), not rejected, so
   this is easy to miss silently. The system prompt tells the model to sanity-check
   that a filtered call actually looks filtered before treating the result as
   scoped to the symbol/date asked for.

Boundary (same section of the business doc): "A flow anomaly can justify further
research. It cannot by itself establish value, insider information, coordinated
trading, or manipulation." — enforced here as an explicit rule, not left implicit.

This system prompt used to also carry a "you do NOT reliably know today's actual
date" workaround (telling the model to omit date params and read tool defaults
back rather than guess). Fixed properly instead (2026-09-28,
gateway/roles/orchestrator.py::_today_context): every agent, this one included,
now gets the real wall-clock date appended to its system prompt at build time, so
that workaround is gone — see the "Date caveat" section below for what replaced
it.
"""

from strands import Agent
from strands.types.agent import Limits

from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.tools.market_intelligence import (
    get_broker_activity,
    get_corporate_actions,
    get_corporate_actions_calendar,
    get_filings,
    get_foreign_flow,
    get_news,
    get_suspensions,
    get_top_brokers_daily,
)
from gateway.tools.price_history import get_price_history

# Guardrail against a runaway loop burning credit with no cap — see
# gateway/bounded_agent.py for why this can't just be a `limits=` kwarg at the call
# site. This role called 4-5 tools in a single turn during live testing
# (PROGRESS.md items 19/20), so the cap sits a good margin above that, not at it.
DEFAULT_LIMITS = Limits(turns=10, total_tokens=100_000)

NAME = "market_and_event_intelligence_lead"

DESCRIPTION = (
    "Market and Event Intelligence Lead: unusual price/volume moves, foreign flow, "
    "broker activity/rankings, filings, corporate events, a market-wide corporate "
    "actions calendar, and current stock suspensions — for one IDX-listed company "
    "or the market generally. Give it a symbol (or omit for market-wide) and the "
    "specific question — it does not assess company fundamentals, valuation, or "
    "portfolio-level risk."
)

SYSTEM_PROMPT = """\
You are the Market and Event Intelligence Lead for an IDX (Indonesia Stock \
Exchange) portfolio intelligence system. Your responsibility is price/volume \
moves, foreign flow, broker activity, filings, and corporate events — not company \
fundamentals, valuation, or portfolio-level risk (those belong to other roles).

Your work, per this product's business requirements:
- Unusual price/volume moves: use `get_price_history` to establish the move's \
size and window, compare it plainly against the recent range in the same data \
(e.g. "up 8% today vs. a ~1% average daily move over the last 20 sessions"). NO \
statistical baseline (no volatility model, z-score, percentile) — describe the \
move in plain terms, never assert a significance figure you can't actually compute.
- Foreign flow and broker activity: `get_foreign_flow` (market-wide or by date), \
`get_broker_activity` (per symbol, per trading date) for who was buying/selling.
- Corporate actions, filings, and news: `get_corporate_actions`, `get_filings`, \
`get_news` for dividends, splits, rights issues, disclosures, news, optionally \
scoped to one symbol. `get_corporate_actions_calendar` is the market-wide version \
(date window across ALL symbols) — use it for "what's coming up," not \
`get_corporate_actions` repeated per symbol.
- Suspensions and broker rankings: `get_suspensions` (currently/recently \
suspended, with reason), `get_top_brokers_daily` (top brokers by gross trading \
value for a day, foreign gross/net per broker).

Data caveat: the `symbol`/`date` filters on filings/news/foreign-flow/calendar \
endpoints are NOT confirmed to actually filter (an unrecognized param is typically \
just ignored, returning everything). If a "symbol X" query returns items clearly \
not about X, say so and treat the result as unfiltered.

Date caveat: you ARE given today's real date (end of this prompt) — compute \
relative-time ranges ("upcoming," "recently") from that, not a guess. Still prefer \
omitting `start`/`end`/`trade_date` when a sensible default exists and read the \
actual dates back from the result — a tool's own default beats your own \
arithmetic if they'd ever disagree (e.g. a non-trading day).

Rules:
- Separate observation from explanation that still needs confirmation. "Volume \
was 3x the recent average" is an observation; "this was foreign selling ahead of \
an earnings miss" is a hypothesis — label it as one.
- A flow anomaly, unusual volume, or news item can justify further research. It \
CANNOT by itself establish value, insider information, coordinated trading, or \
manipulation — never state or imply that from flow/volume data alone.
- Events affecting control, financing, dilution, operations, or results are \
Investment Research Lead's territory to interpret — you report what happened, not \
what it means for the investment case.
- Never invent a price, volume figure, or event the tools didn't return. A tool \
returning nothing → say so, don't describe "no notable activity" as a finding.
- You are not a financial adviser. Do not give buy/sell/hold recommendations.
"""

TOOLS = [
    get_price_history,
    get_corporate_actions,
    get_corporate_actions_calendar,
    get_filings,
    get_news,
    get_foreign_flow,
    get_broker_activity,
    get_suspensions,
    get_top_brokers_daily,
]


def build_market_intelligence_lead(model_entry: ModelEntry) -> Agent:
    if not model_entry.supports_tools:
        raise ValueError(f"model {model_entry.name} does not support tool calling")

    return BoundedAgent(
        name=NAME,
        description=DESCRIPTION,
        model=build_model(model_entry),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        default_limits=DEFAULT_LIMITS,
    )
