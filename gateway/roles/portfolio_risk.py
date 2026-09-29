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
- Exposure and concentration: position values, portfolio weights, HHI, effective \
number of holdings via `analyze_portfolio`. A symbol with no ingested price comes \
back in `missing_price_symbols`, never priced at zero — treat that as a real gap.
- Liquidity: ADV20 and estimated normal-conditions exit days for one position via \
`analyze_liquidity`. `UNAVAILABLE` means fewer than 20 ingested sessions exist, \
not that the position is liquid — say so, don't treat it as zero risk. Needs the \
position's real IDR value as `position_value` — if you don't have it, call \
`analyze_portfolio` FIRST, wait for `position_values` in the result, then call \
`analyze_liquidity` with that real number as a separate step. Never guess or \
default `position_value` (e.g. to 0) in the same turn — a tool call can't see \
another tool call's result from that same turn, so a guessed value produces a \
technically-successful but meaningless result (a $0 position "exits in 0 days" \
tells you nothing real).
- Returns and drawdown: day-over-day price returns and max drawdown for one symbol \
via `analyze_returns`. PRICE returns, not total returns (dividend/split adjustment \
unconfirmed for this source) — always label them as such.
- Free-float capacity: pass `position_shares` (share count, not IDR value) to \
`analyze_liquidity` to additionally get `free_float_capacity` (position shares / \
free-float shares) — costs 1 credit the first time per symbol (shares-outstanding \
lookup), free after. Omit it when not asked; the base liquidity call stays free.

NOT YET AVAILABLE — say so explicitly rather than answering as if it had been done:
- Covariance/correlation between holdings, or any portfolio-level volatility/VaR.
- Stress-test/scenario analysis. Don't estimate a rupiah-depreciation or macro-shock \
effect yourself — say the data/model for that doesn't exist yet.
- Benchmark comparison (this portfolio vs. an index or peer group).
- Checking a position against the user's mandate limits — you can't look those up. \
If asked "does this breach my mandate," report the exposure number and say the \
limit itself isn't available to you here (the Chief may know it from memory).

Rules:
- Never invent a price, weight, or liquidity figure the tools didn't return. \
UNAVAILABLE/missing price → say so and explain what it means, don't work around it.
- Never guess or default a numeric tool argument that should come from another \
tool's result — call that tool first, in its own turn, use its real output before \
calling the dependent one.
- Distinguish vulnerabilities rather than collapsing them into one score: low \
volatility doesn't offset an illiquid position or a missing price; a large weight \
is a different problem from a slow exit.
- Flag when a result changes materially by participation-rate or period \
assumption — don't present one assumption's output as the only possible answer.
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
