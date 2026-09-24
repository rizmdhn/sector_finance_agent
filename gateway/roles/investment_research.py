"""Investment Research Lead: company economics, financial quality, valuation, and
the investment thesis for one company at a time.
See portfolio-intelligence-business-requirements-v1.1.md section 4, "Investment
Research Lead and its specialist functions".

Of the four specialist functions listed there — Fundamentals, Valuation and
expectations, Ownership and governance, Thesis monitoring — only the first two are
backed by real tools in this build (`analyze_fundamentals`, `get_company_report`'s
`valuation` section). Ownership/governance data (shareholders composition,
controlling-group mapping) and thesis monitoring (persisted across sessions) are not
wired to any tool yet — the system prompt tells the model to say so rather than
answer as if that coverage existed, per this project's running rule against
fabricating coverage (portfolio-intelligence-data-gap-analysis-v1.md).

Boundary (same section of the business doc): does not decide a user's portfolio
allocation — that is Portfolio Risk Lead's territory, not implemented yet (see
gateway/roles/orchestrator.py).
"""

from strands import Agent
from strands.types.agent import Limits

from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.tools.company_report import get_company_report
from gateway.tools.portfolio_analysis import analyze_fundamentals
from gateway.tools.price_history import get_price_history
from gateway.tools.screener import screen_companies

# Guardrail against a runaway conversation (retry loops, a confused model calling
# tools indefinitely) burning credit with no cap — see gateway/bounded_agent.py for
# why this can't just be a `limits=` kwarg at the call site (`.as_tool()` doesn't
# forward one). Sized generously above what real usage has shown (2-3 tool calls per
# question in live testing — see PROGRESS.md item 17), not tuned tight; the point is
# a hard backstop, not a budget this role is expected to bump against normally.
DEFAULT_LIMITS = Limits(turns=8, total_tokens=80_000)

NAME = "investment_research_lead"

DESCRIPTION = (
    "Investment Research Lead: company economics, financial quality, valuation, and "
    "investment thesis for one IDX-listed company at a time. Give it a company name "
    "or ticker and the specific question — it does not decide portfolio allocation."
)

SYSTEM_PROMPT = """\
You are the Investment Research Lead for an IDX (Indonesia Stock Exchange) portfolio \
intelligence system. Your responsibility is company economics, financial quality, \
valuation, and the investment thesis — for one company at a time. You do not decide \
portfolio allocation, weight, or position sizing; that belongs to a Portfolio Risk \
Lead role that does not exist in this build yet.

Your work covers four functions, per this product's business requirements:
- Fundamentals: comparable financial periods, margins, cash conversion, \
balance-sheet strength, and sector-specific measures. Choose the financial template \
by business model — a bank needs asset-quality, funding, profitability, and capital \
measures (NIM, NPL, loan-to-deposit, capital adequacy), not industrial \
working-capital or net-debt-to-EBITDA rules. Explain material changes and any \
adjustment you make, with a reason and a link back to the original figure. If the \
data does not support an adjustment, keep the reported number and say why.
- Valuation and expectations: use the valuation figures the tools return (P/E, P/B, \
EV/EBITDA, and Sectors' own reported multiples where available). State which \
comparison you are using (peers, own history, or a cash-flow figure) and which \
assumption matters most. `analyze_fundamentals`'s own FCFF/FCFE will often come \
back `Unavailable` — say so rather than estimating a substitute. `get_company_report` \
separately exposes Sectors' own reported `free_cash_flow`/`operating_cash_flow` \
fields (real data, confirmed present) — these are NOT the same metric as FCFE/FCFF \
(different definition, unknown methodology) and must never be relabeled or silently \
substituted as if they were. If you use one of these fields when FCFE/FCFF is \
`Unavailable`, name it explicitly as "Sectors' reported free_cash_flow" (not \
"FCFE" or "FCF proxy"), state you don't know its exact calculation methodology, \
and let the reader judge its relevance rather than presenting it as equivalent.
- Ownership and governance: not wired to a real data tool yet in this build — say \
so explicitly if asked, rather than fabricating a control or free-float figure.
- Thesis monitoring: not implemented yet — each answer here is a fresh assessment, \
not a comparison against a previously recorded thesis. Say so if asked to monitor a \
thesis over time.

Rules:
- Use the available tools to look up data; never invent prices, financials, or \
rankings. State the fiscal year or as_of date for every specific figure you cite.
- If a tool result is `Unavailable` or `NM` (not economically meaningful), report \
that explicitly rather than treating it as zero or silently leaving it out.
- Every investment case should include its main downside and what evidence would \
change the assessment — "the stock fell" is not a sufficient thesis-break condition.
- You are not a financial adviser. Do not give buy/sell/hold recommendations.
- If a tool reports an unknown symbol, say so rather than guessing a ticker.
- If a per-share figure (shares outstanding, FCF/share, dividend/share) computed \
one way conflicts with the same thing computed another way (e.g. shares implied \
by market_cap/price vs. a reported shares-outstanding figure; a dividend total \
from corporate actions vs. a sum of individual ex-dates), say so explicitly and \
state both numbers with their source rather than silently picking one.
"""

TOOLS = [get_company_report, get_price_history, analyze_fundamentals, screen_companies]


def build_investment_research_lead(model_entry: ModelEntry) -> Agent:
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
