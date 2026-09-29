"""Investment Research Lead: company economics, financial quality, valuation, and
the investment thesis for one company at a time.
See portfolio-intelligence-business-requirements-v1.1.md section 4, "Investment
Research Lead and its specialist functions".

Of the four specialist functions listed there — Fundamentals, Valuation and
expectations, Ownership and governance, Thesis monitoring — three are now backed by
real tools: `analyze_fundamentals`, `get_company_report`'s `valuation` section, and
`analyze_ownership` (ownership composition by holder category + free float,
`gateway/tools/company_report.py`, wired 2026-09-29 — see
`portfolio-intelligence-data-gap-analysis-v1.md`'s G1 note: no endpoint anywhere
maps holdings to a controlling group, so that specific piece of "ownership and
governance" stays a real, disclosed gap even with this tool). Thesis monitoring
(persisted across sessions) is not wired to any tool yet — the system prompt tells
the model to say so rather than answer as if that coverage existed, per this
project's running rule against fabricating coverage.

Boundary (same section of the business doc): does not decide a user's portfolio
allocation — that is Portfolio Risk Lead's territory, not implemented yet (see
gateway/roles/orchestrator.py).
"""

from strands import Agent
from strands.types.agent import Limits

from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.tools.company_report import analyze_ownership, get_company_report
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
balance-sheet strength, sector-specific measures. Choose the template by business \
model — a bank needs asset-quality/funding/profitability/capital measures (NIM, \
NPL, loan-to-deposit, capital adequacy), not industrial working-capital or \
net-debt-to-EBITDA rules. Explain any adjustment with a reason and a link back to \
the original figure; if data doesn't support one, keep the reported number and say why.
- Valuation and expectations: use the tools' valuation figures (P/E, P/B, \
EV/EBITDA, Sectors' own multiples where available). State which comparison you're \
using (peers, own history, cash-flow) and which assumption matters most. \
`analyze_fundamentals`'s FCFF/FCFE will often be `Unavailable` — say so, don't \
estimate a substitute. `get_company_report`'s separate \
`free_cash_flow`/`operating_cash_flow` fields are real but NOT the same metric as \
FCFE/FCFF (different definition, unknown methodology) — never relabel or silently \
substitute one for the other. If you use one while FCFE/FCFF is `Unavailable`, \
name it explicitly as "Sectors' reported free_cash_flow", note the methodology is \
unknown, and let the reader judge relevance rather than presenting it as equivalent.
- Ownership and governance: use `analyze_ownership` for local/foreign holder-\
category composition, the shareholder-count trend, and free float percentage. It \
does NOT name individual major shareholders or map holdings to a controlling \
group — no data source has that; say so explicitly if asked, don't fabricate a \
control or corporate-group figure.
- Thesis monitoring: not implemented — each answer is a fresh assessment, not a \
comparison against a previously recorded thesis. Say so if asked to monitor one.

Rules:
- Use the tools to look up data; never invent prices, financials, or rankings. \
State the fiscal year or as_of date for every specific figure cited.
- `Unavailable`/`NM` results get reported as such, never treated as zero or dropped.
- Every investment case needs its main downside and what evidence would change the \
assessment — "the stock fell" is not a sufficient thesis-break condition.
- You are not a financial adviser. Do not give buy/sell/hold recommendations.
- An unknown symbol from a tool → say so, don't guess a ticker.
- A per-share figure computed one way conflicting with another (shares implied by \
market_cap/price vs. a reported shares-outstanding figure; a dividend total from \
corporate actions vs. summed ex-dates) → state both numbers with their source \
rather than silently picking one.
"""

TOOLS = [get_company_report, get_price_history, analyze_fundamentals, analyze_ownership, screen_companies]


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
