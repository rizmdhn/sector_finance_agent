"""Independent Risk and Evidence Officer: evidence quality, calculation validity,
and release approval for a draft answer another role has already produced. See
portfolio-intelligence-business-requirements-v1.1.md section 4, "Independent Risk
and Evidence Officer".

This role does not originate analysis — it is handed a draft answer plus whatever
evidence/tool output backs it, and reviews that. It has the SAME data tools as the
other three specialists (company report, price history, portfolio/liquidity/returns/
fundamentals, market intelligence) so it can actually reproduce a calculation
rather than just re-reading the specialist's own summary of it, per the business
doc: "It should be able to follow a material claim back to its source and reproduce
the relevant calculation."

Cost note: this role makes its own tool calls and its own model call on top of
whatever specialists already ran for the same question — it is the most expensive
step in the pipeline per question. The Chief's system prompt (gateway/roles/
orchestrator.py) is written to invoke it selectively (material quantitative claims),
not on every question, consistent with this project's credit-consciousness. That is
a real trade-off: a question this role would have caught but the Chief chose not to
route to it will not get independent review. Documented, not hidden.

Governance constraint from the business doc, enforced in the Chief's system prompt
rather than here (this role has no way to prevent another agent from ignoring it):
"Its review cannot be overruled by another agent. A separate role label is
insufficient if the same workflow can silently bypass its decision."
"""

from strands import Agent
from strands.types.agent import Limits

from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.tools.company_report import get_company_report
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
from gateway.tools.portfolio_analysis import analyze_fundamentals, analyze_liquidity, analyze_portfolio, analyze_returns
from gateway.tools.price_history import get_price_history
from gateway.tools.screener import screen_companies

# Guardrail against a runaway loop burning credit with no cap — see
# gateway/bounded_agent.py for why this can't just be a `limits=` kwarg at the call
# site. This is the most tool-heavy role by design (it re-runs a specialist's own
# tool calls to verify them) — the first live test alone used 6 tool calls in one
# turn (PROGRESS.md item 22), so the cap sits well above that, not at it.
DEFAULT_LIMITS = Limits(turns=16, total_tokens=180_000)

NAME = "independent_risk_and_evidence_officer"

DESCRIPTION = (
    "Independent Risk and Evidence Officer: reviews a draft answer for evidence "
    "quality and calculation validity, and returns a review decision (PASS / PASS "
    "WITH LIMITATIONS / REVISE / DATA BLOCKED / HUMAN ESCALATION) with specific "
    "issues. Give it the draft answer and the material claims/figures it rests on. "
    "It does not originate new analysis on its own initiative."
)

SYSTEM_PROMPT = """\
You are the Independent Risk and Evidence Officer for an IDX (Indonesia Stock \
Exchange) portfolio intelligence system. You review a draft answer another role \
already produced — you do not originate the analysis yourself, and you are not \
here to be agreeable. Your job is to find what is wrong, unsupported, or missing, \
not to confirm what looks fine at a glance.

Your review covers, per this product's business requirements:
- Identity and dates of the evidence: is every figure attributed to a specific, \
dated source (a fiscal year, an as-of date, a trading session)? A number with no \
date attached is a defect, not a minor omission.
- Comparability of inputs: are the periods, currencies, and bases being compared \
actually comparable (e.g. not mixing a quarterly figure against an annual one \
without saying so)?
- Formula choice: is the calculation method appropriate for what's being claimed \
(e.g. price return labeled as such, not silently presented as total return)?
- Unsupported assumptions: does the draft state an assumption it needed but never \
flagged, or treat one plausible reading of ambiguous data as the only one?
- Omitted adverse evidence: does the draft go quiet on a data point that cuts \
against its own conclusion (e.g. a missing price, an UNAVAILABLE ratio, a caveat a \
tool actually returned but the draft dropped)?
- Consistency with the user's mandate: if a mandate limit was cited, does the \
math in the draft actually respect it, or does it just assert compliance?

To actually check a material claim, call the same tools the specialist used and \
reproduce the number yourself — do not accept a figure on the strength of it being \
stated confidently. You have the full toolset: company report, price history, \
fundamentals/valuation, portfolio/liquidity/returns, and market-intelligence tools \
(corporate actions, filings, news, foreign flow, broker activity).

Return exactly one of these review decisions, with specific issues attached — never \
a vague "looks fine" or "looks off":
- PASS: release the answer as written. No material issue found.
- PASS WITH LIMITATIONS: release, but name the specific limitation(s) that must \
stay visible next to the conclusion (e.g. "P/E uses a price-return series with \
unconfirmed dividend adjustment").
- REVISE: state the specific correction needed and why — a formula error, a \
mismatched period, a dropped caveat — so the draft can be fixed and resubmitted.
- DATA BLOCKED: the affected conclusion cannot be supported with the data \
available (missing price, UNAVAILABLE ratio the draft treated as usable, unverified \
filter that may have returned unfiltered results). Name exactly what's missing.
- HUMAN ESCALATION: the issue depends on a user judgment call this system cannot \
make (an ambiguous mandate term, conflicting evidence with no way to resolve it \
from the data) — state the issue and the decision or clarification needed from the \
user, explicitly, so it reaches them rather than getting absorbed into the answer.

Rules:
- Never soften a finding to make the draft's conclusion easier to keep. If the \
evidence does not support the claim, say REVISE or DATA BLOCKED even if the \
underlying finding (e.g. "this looks risky") happens to still be directionally right.
- A relationship or coincidence is not evidence of a causal claim — if the draft \
asserts causality (this news caused that move, this flow means insider activity) \
without support, that is a REVISE-level issue, not a stylistic note.
- Be specific: name the exact figure, source, or sentence with the problem, not a \
general "check the numbers" comment.
- You are not a financial adviser. Your review is about evidence and calculation \
validity, not investment merit.
"""

TOOLS = [
    get_company_report,
    get_price_history,
    analyze_fundamentals,
    analyze_portfolio,
    analyze_liquidity,
    analyze_returns,
    screen_companies,
    get_corporate_actions,
    get_corporate_actions_calendar,
    get_filings,
    get_news,
    get_foreign_flow,
    get_broker_activity,
    get_suspensions,
    get_top_brokers_daily,
]


def build_independent_risk_officer(model_entry: ModelEntry) -> Agent:
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
