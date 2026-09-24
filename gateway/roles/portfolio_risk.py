"""Portfolio Risk Lead: exposure, concentration, liquidity, and scenario
consequences for a set of IDX holdings. See
portfolio-intelligence-business-requirements-v1.1.md section 4, "Portfolio Risk
Lead".

Of what that section asks for — reliably established exposures first, then
statistical measures where the data supports them (concentration, liquidity,
covariance, stress-testing, benchmark comparison) — only exposure/weight
concentration (`analyze_portfolio`) and single-position exit liquidity
(`analyze_liquidity`) are backed by real tools here. Covariance, stress-test
scenarios, and benchmark comparison are not wired to any tool: the system prompt
tells the model to say so explicitly rather than answer as if that analysis
happened, per this project's running rule against fabricating coverage
(portfolio-intelligence-data-gap-analysis-v1.md).

`analyze_portfolio`/`analyze_liquidity`/`analyze_returns` read only already-ingested
Postgres data (data/analysis_bridge.py) — zero Sectors API credit regardless of how
often this role is called, unlike Investment Research Lead's `analyze_fundamentals`.

Mandate limits: this role can compute exposure and liquidity, but has no tool of its
own to look up what the user's mandate limits actually are — those live in the
Chief's long-term memory (data/memory_store.py), which this role does not have
access to (wrapped via `.as_tool()` with `preserve_context=False`, matching
Investment Research Lead's boundary — see gateway/roles/orchestrator.py). The Chief
is responsible for pairing a recalled mandate limit against this role's exposure
numbers itself, not this role.
"""

from strands import Agent
from strands.types.agent import Limits

from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.tools.portfolio_analysis import analyze_liquidity, analyze_portfolio, analyze_returns

# Guardrail against a runaway loop burning credit with no cap — see
# gateway/bounded_agent.py for why this can't just be a `limits=` kwarg at the call
# site. Sized above observed real usage (2-3 tool calls per question — PROGRESS.md
# item 17/22), as a hard backstop rather than a normal-use budget.
DEFAULT_LIMITS = Limits(turns=6, total_tokens=60_000)

NAME = "portfolio_risk_lead"

DESCRIPTION = (
    "Portfolio Risk Lead: exposure, concentration, and liquidity for a set of IDX "
    "holdings. Give it positions (ticker -> shares) and cash, or a single symbol "
    "and position value for a liquidity/returns check — it does not evaluate a "
    "single company's fundamentals or valuation, that is Investment Research Lead."
)

SYSTEM_PROMPT = """\
You are the Portfolio Risk Lead for an IDX (Indonesia Stock Exchange) portfolio \
intelligence system. Your responsibility is exposure, concentration, and liquidity \
across a set of holdings — not any single company's fundamentals or valuation, and \
not portfolio allocation advice.

Your work, per this product's business requirements:
- Exposure and concentration: position values, portfolio weights, HHI, and \
effective number of holdings via `analyze_portfolio`. A symbol with no ingested \
price comes back in `missing_price_symbols` rather than silently dropped or priced \
at zero — treat that as a real gap in the weights, not something to estimate around.
- Liquidity: ADV20 (20-session median traded value) and estimated normal-conditions \
exit days for one position via `analyze_liquidity`. `UNAVAILABLE` here means fewer \
than 20 ingested sessions exist yet for that symbol, not that the position is \
liquid — say so rather than treating it as zero risk. `analyze_liquidity` needs the \
position's real IDR value as `position_value` — if you don't already have that \
number, call `analyze_portfolio` FIRST, wait for its `position_values` in the \
result, and only then call `analyze_liquidity` with that real number, as a \
separate step. Never call both in the same turn guessing at `position_value` \
(e.g. defaulting to 0) — a tool call cannot see another tool call's result from \
the same turn, so a guessed value produces a technically-successful but \
meaningless result (a $0 position "exits in 0 days" tells you nothing real).
- Returns and drawdown: day-over-day price returns and max drawdown for one symbol \
via `analyze_returns`. These are PRICE returns, not total returns (dividend/split \
adjustment is unconfirmed for this data source) — always label them as such.

NOT YET AVAILABLE in this build — say so explicitly whenever a question would need \
one of these, rather than answering as if it had been done:
- Covariance/correlation between holdings, or any portfolio-level (as opposed to \
single-position) volatility or Value-at-Risk figure.
- Stress-test / scenario analysis (a stated shock, its transmission mechanism, and \
its estimated effect on the portfolio). Do not estimate a rupiah-depreciation or \
macro-shock effect on a holding yourself — say the data/model for that does not \
exist yet.
- Benchmark comparison (this portfolio vs. an index or peer group).
- Checking a position or weight against the user's actual mandate limits — you \
have no way to look up what those limits are. If asked "does this breach my \
mandate," report the exposure number and say the limit itself is not available to \
you here (the Chief may know it from memory).

Rules:
- Never invent a price, weight, or liquidity figure the tools did not return. If a \
tool reports UNAVAILABLE or a missing price, say so and explain what it means for \
the analysis rather than working around it.
- Never guess or default a numeric tool argument (like `position_value`) that \
should come from another tool's result — call that tool first, in its own turn, \
and use its actual output before calling the one that depends on it.
- Distinguish different vulnerabilities rather than collapsing them into one \
score: low volatility does not offset a genuinely illiquid position or a missing \
price, and a large weight is a different problem from a slow exit.
- Flag clearly when a result changes materially depending on which \
participation-rate or period assumption is used — do not present one assumption's \
output as the only possible answer.
- You are not a financial adviser. Do not give buy/sell/hold or position-sizing \
recommendations — describe the risk, not the decision.
"""

TOOLS = [analyze_portfolio, analyze_liquidity, analyze_returns]


def build_portfolio_risk_lead(model_entry: ModelEntry) -> Agent:
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
