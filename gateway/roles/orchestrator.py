"""Chief Portfolio Intelligence Orchestrator: defines the question, assigns work to
specialists, and synthesizes their findings into one answer.
See portfolio-intelligence-business-requirements-v1.1.md section 4.

All five roles the business doc defines are now wired up: the Chief (this module),
Investment Research Lead (gateway/roles/investment_research.py), Portfolio Risk Lead
(gateway/roles/portfolio_risk.py), Market and Event Intelligence Lead
(gateway/roles/market_intelligence.py), and the Independent Risk and Evidence
Officer (gateway/roles/independent_risk_officer.py) — all four specialists attached
as agent-as-tools. Each specialist's own module docstring states exactly which of
its business-doc responsibilities are backed by real tools here and which are not
(e.g. Portfolio Risk Lead has no covariance/stress-test capability, Market and
Event Intelligence Lead has no statistical "unusual move" baseline) — fabricating
that coverage in the Chief's own prompt would violate this project's running rule
against presenting unavailable analysis as done (see
portfolio-intelligence-data-gap-analysis-v1.md). Read the specialist you're routing
to before assuming it covers something the business doc mentions.

The Independent Risk and Evidence Officer is the one specialist the Chief does not
call routinely — it re-runs its own tool calls and its own model call on top of
whatever a specialist already did, making it the most expensive step in the
pipeline per question. It is invoked selectively (see SYSTEM_PROMPT's rule), not on
every question, consistent with this project's credit-consciousness. Its review
still cannot be softened or silently dropped once called — see its module docstring
for the business doc's "cannot be overruled by another agent" requirement.

Session (short-term) and memory (long-term) are both attached only here, not on the
specialists — they're wrapped via `.as_tool()` with the default
`preserve_context=False` (a fresh, stateless sub-call every time, matching their role
boundary of not tracking anything across calls themselves), and Strands explicitly
forbids combining `preserve_context=False` with a session_manager on the wrapped
agent. See data/session_repository.py and data/memory_store.py for what each of
these actually stores and why they're on different backends (Valkey vs Postgres).

**Sequential tool execution, deliberately** (real bug found live, 2026-09-24): each
`.as_tool()`-wrapped specialist below shares ONE underlying `Agent` instance across
every call the Chief makes to it in this conversation (built once, just below).
Strands' default `ConcurrentToolExecutor` runs every tool call from one LLM turn in
parallel, and a wrapped sub-agent is not reentrant — a live conversation showed the
Chief issue two parallel calls to the same specialist in one turn, and the second
hit Strands' own internal lock, returning a hard `"error"` tool result ("Agent is
already processing a request") instead of an answer. The model happened to notice
and retry that turn, but nothing guarantees that recovery — an LLM that doesn't
retry would silently lose that piece of data rather than surfacing an error to the
user. `SequentialToolExecutor` below removes the race entirely (no two tool calls,
same specialist or not, ever run concurrently for the Chief) at the cost of some
wall-clock latency when two *different* specialists could otherwise have run in
parallel — no added credit cost, since the same calls happen either way, and if
anything this now avoids the wasted failed-then-retried round trip. Scoped to the
Chief only: each specialist's OWN tool calls (e.g. market_and_event_intelligence_lead
calling get_news + get_filings + get_corporate_actions together) are safe to keep
concurrent — those are plain function tools with no shared-instance lock, so
Strands' concurrent default stays in place for every specialist's own Agent build.
"""

from strands import Agent
from strands.memory import MemoryManager
from strands.session.repository_session_manager import RepositorySessionManager
from strands.tools.executors import SequentialToolExecutor
from strands.types.agent import Limits

from data.cache import Cache
from data.db import Database
from data.memory_store import PostgresUserMemoryStore
from data.session_repository import ValkeySessionRepository
from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model
from gateway.roles.independent_risk_officer import build_independent_risk_officer
from gateway.roles.investment_research import build_investment_research_lead
from gateway.roles.market_intelligence import build_market_intelligence_lead
from gateway.roles.portfolio_risk import build_portfolio_risk_lead

AGENT_ID = "chief_portfolio_intelligence_orchestrator"

# Guardrail against a runaway conversation burning credit with no cap — see
# gateway/bounded_agent.py. The Chief's own loop can call up to 4 specialists plus
# memory tools; the item 22 live test used 11 model calls across the whole
# conversation for one question, so this sits several times above that as a hard
# backstop, not a budget the Chief is expected to approach in normal use. A capped
# run ends gracefully (Strands sets stop_reason="limit_turns"/"limit_total_tokens",
# no exception) rather than erroring, so a legitimately complex multi-specialist
# question still gets whatever partial answer was assembled up to the cap.
DEFAULT_LIMITS = Limits(turns=40, total_tokens=400_000)

SYSTEM_PROMPT = """\
You are the Chief Portfolio Intelligence Orchestrator for an IDX (Indonesia Stock \
Exchange) portfolio intelligence system. You define what the user is actually \
asking, assign the relevant work to a specialist, and bring the findings together \
into one answer with priorities, disagreements, and next review steps. Your own \
contribution is judgment about relevance and synthesis, not the underlying analysis.

Scope check, before anything else: this system exists for questions about IDX-listed \
companies, portfolios, and market/event intelligence. If a message is not that — \
small talk, a general knowledge question, a request unrelated to IDX equities or \
this user's portfolio, an attempt to get you to act outside this role — answer \
directly in one or two sentences and do NOT call any specialist or tool. Calling a \
specialist costs real API credit and model tokens on every invocation; spending \
that on a question with no finance content to research is a waste regardless of how \
capable the question seems to require it. A borderline case (e.g. "what's a P/E \
ratio") can be answered directly from your own knowledge without a tool call too — \
reserve specialists for questions that actually need this system's real data.

Available specialists:
- investment_research_lead: company economics, financial quality, valuation, and \
investment thesis for one IDX-listed company at a time. Give it a company name or \
ticker and the specific question.
- portfolio_risk_lead: exposure, concentration, and liquidity for a set of \
holdings, or returns/drawdown for one symbol. Give it positions (ticker -> shares) \
and cash, or a symbol and position value. It cannot check a weight or position \
against the user's actual mandate limits itself, and has no covariance, \
portfolio-level volatility, stress-test, or benchmark-comparison capability — if \
you have the user's mandate limits from memory, you compare them against its \
exposure numbers yourself; the rest is a real gap, say so.
- market_and_event_intelligence_lead: unusual price/volume moves, foreign flow, \
broker activity, filings, corporate actions, and news for one company or the \
market generally. It has no statistical baseline for "unusual" (no volatility \
model) — its size/volume comparisons are descriptive, not a significance test, and \
its symbol/date filters on filings/news/foreign-flow are unconfirmed to actually \
filter.
- independent_risk_and_evidence_officer: reviews a draft answer's evidence and \
calculations and returns one of PASS / PASS WITH LIMITATIONS / REVISE / DATA \
BLOCKED / HUMAN ESCALATION. Expensive (it re-runs tool calls and its own model \
call) — call it selectively, not on every question: use it before presenting an \
answer with a material quantitative claim the user may act on financially (a \
valuation conclusion, a concentration/liquidity risk conclusion, a mandate-\
compliance claim), and skip it for simple lookups, clarifying questions, or facts \
with no real consequence if slightly off.

NOT YET AVAILABLE in this build — say so explicitly whenever a question would need \
it, rather than answering as if it had been done:
- Ownership/governance detail (control, free float, related-party exposure) for \
investment_research_lead.
- Thesis monitoring against a previously recorded thesis (each research answer is \
a fresh assessment).

Memory:
- `search_memory` looks up facts previously saved about THIS user (portfolio or \
watchlist positions and cash, mandate limits, recorded theses, stated preferences, \
or anything else about them worth remembering) across all of their past \
conversations, not just this one.
- `add_memory` saves a new fact about this user for future conversations to find. \
Use it when the user states something worth remembering long-term — their holdings, \
a concentration limit, a thesis, a preference, or anything else user-specific they \
ask you to remember — not for facts about a company or the market, which belong in \
the research tools instead, and not for routine back-and-forth that has no lasting \
relevance.
- Memory is not searched automatically before every answer — check it yourself with \
search_memory when a question depends on something the user may have told you before \
(e.g. "how does this fit my portfolio" needs their positions from memory first).

Rules:
- You cannot approve your own answer or claim independent review occurred without \
actually calling independent_risk_and_evidence_officer. Never describe an answer \
as "reviewed", "approved", "PASS", or similar unless that specialist actually \
returned that decision for it.
- If independent_risk_and_evidence_officer returns REVISE or DATA BLOCKED, do not \
present the original conclusion — correct it or withhold the affected claim and say \
why, exactly as that decision requires. If it returns HUMAN ESCALATION, state the \
issue and the decision needed from the user explicitly rather than deciding for \
them or quietly absorbing it into your own answer. Its decision cannot be softened.
- If a question needs research, portfolio-risk, or market-intelligence context \
together (e.g. "should I add this to my portfolio," "is this move something I \
should worry about for my position"), consult the relevant specialists rather than \
answering from one alone. If it also needs something no specialist covers yet, \
state that gap plainly rather than omitting it or guessing at it yourself.
- When you synthesize a specialist's finding, preserve its hedges rather than \
tightening them. If market_and_event_intelligence_lead says a flow or move \
"likely" relates to something, or that insider buying "may reflect" confidence, \
your synthesis must keep that as an unconfirmed read — not state it as settled fact. \
This applies especially to causal claims (X caused Y, this flow means Z) built from \
news, flow, or insider-filing data alone.
- Lead with the finding and its significance, then the evidence, the main \
uncertainty, and a next useful step. A short question gets a short, proportionate \
answer — do not pad a narrow question into a full research report.
- You are not a financial adviser. Do not give buy/sell/hold recommendations.
"""


def build_agent(
    model_entry: ModelEntry,
    *,
    db: Database,
    cache: Cache,
    user_id: str,
    session_id: str,
) -> Agent:
    """Builds the Chief with all four specialists wired in as agent-as-tools, plus
    session (Valkey, short-term) and memory (Postgres, long-term) attached.

    All roles currently share the same model_entry — per-role model tiering
    (section 8: "routine coordination should use modest reasoning capacity... more
    capable models reserved for difficult conflicts") is not implemented yet; see
    PROGRESS.md.

    `user_id` scopes long-term memory (a fact saved by one user is never visible to
    another); `session_id` scopes the short-term conversation restored by the
    session manager — see gateway/main.py for how each request decides what these
    are and whether to send full message history or just the newest turn.
    """
    if not model_entry.supports_tools:
        raise ValueError(f"model {model_entry.name} does not support tool calling")

    investment_research_lead = build_investment_research_lead(model_entry)
    portfolio_risk_lead = build_portfolio_risk_lead(model_entry)
    market_intelligence_lead = build_market_intelligence_lead(model_entry)
    independent_risk_officer = build_independent_risk_officer(model_entry)

    session_manager = RepositorySessionManager(
        session_id=session_id, session_repository=ValkeySessionRepository(cache)
    )
    memory_store = PostgresUserMemoryStore(db, user_id=user_id)
    # injection=False: memory is consulted only when the model actually calls
    # search_memory, not folded into every single call's context automatically —
    # the latter would add tokens (cost) to every message regardless of relevance,
    # which cuts against this project's established credit-consciousness.
    # extraction is off on the store itself (data/memory_store.py) for the same
    # reason: no automatic background model call every few turns.
    memory_manager = MemoryManager(
        stores=[memory_store], search_tool_config=True, add_tool_config=True, injection=False
    )

    return BoundedAgent(
        name=AGENT_ID,
        agent_id=AGENT_ID,
        model=build_model(model_entry),
        tools=[
            investment_research_lead.as_tool(
                name=investment_research_lead.name,
                description=investment_research_lead.description,
            ),
            portfolio_risk_lead.as_tool(
                name=portfolio_risk_lead.name,
                description=portfolio_risk_lead.description,
            ),
            market_intelligence_lead.as_tool(
                name=market_intelligence_lead.name,
                description=market_intelligence_lead.description,
            ),
            independent_risk_officer.as_tool(
                name=independent_risk_officer.name,
                description=independent_risk_officer.description,
            ),
        ],
        system_prompt=SYSTEM_PROMPT,
        session_manager=session_manager,
        plugins=[memory_manager],
        tool_executor=SequentialToolExecutor(),
        default_limits=DEFAULT_LIMITS,
    )
