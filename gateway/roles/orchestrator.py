"""Chief Portfolio Intelligence Orchestrator: defines the question, assigns work to
specialists, and synthesizes their findings into one answer.
See portfolio-intelligence-business-requirements-v1.1.md section 4.

Of the five roles the business doc defines, only two are wired up so far: the Chief
(this module) and Investment Research Lead (gateway/roles/investment_research.py, as
an agent-as-tool). Portfolio Risk Lead, Market and Event Intelligence Lead, and the
Independent Risk and Evidence Officer are not implemented — see PROGRESS.md. The
system prompt tells the model to say so explicitly whenever a question would need
one of them, rather than answering as though a portfolio-risk check or an
independent review had happened. Fabricating that coverage would violate this
project's running rule against presenting unavailable analysis as done (see
portfolio-intelligence-data-gap-analysis-v1.md) — it's the same principle, applied to
missing *roles* instead of missing *data fields*.

A deliberate consequence: the flat MVP agent this replaced (see git history) had
direct tool access to analyze_portfolio/analyze_liquidity/analyze_returns. Those are
Portfolio Risk Lead's territory per the doc's role table, so the Chief does not get
them directly here — that capability is temporarily unavailable at the top level
until Portfolio Risk Lead exists, rather than exposed through a role boundary it
doesn't belong to.

Session (short-term) and memory (long-term) are both attached only here, not on
Investment Research Lead — IRL is wrapped via `.as_tool()` with the default
`preserve_context=False` (a fresh, stateless sub-call every time, matching its role
boundary of not tracking anything across calls itself), and Strands explicitly
forbids combining `preserve_context=False` with a session_manager on the wrapped
agent. See data/session_repository.py and data/memory_store.py for what each of
these actually stores and why they're on different backends (Valkey vs Postgres).
"""

from strands import Agent
from strands.memory import MemoryManager
from strands.session.repository_session_manager import RepositorySessionManager

from data.cache import Cache
from data.db import Database
from data.memory_store import PostgresUserMemoryStore
from data.session_repository import ValkeySessionRepository
from gateway.registry import ModelEntry, build_model
from gateway.roles.investment_research import build_investment_research_lead

AGENT_ID = "chief_portfolio_intelligence_orchestrator"

SYSTEM_PROMPT = """\
You are the Chief Portfolio Intelligence Orchestrator for an IDX (Indonesia Stock \
Exchange) portfolio intelligence system. You define what the user is actually \
asking, assign the relevant work to a specialist, and bring the findings together \
into one answer with priorities, disagreements, and next review steps. Your own \
contribution is judgment about relevance and synthesis, not the underlying analysis.

Available specialist:
- investment_research_lead: company economics, financial quality, valuation, and \
investment thesis for one IDX-listed company at a time. Give it a company name or \
ticker and the specific question.

NOT YET AVAILABLE in this build — say so explicitly whenever a question would need \
one of these, rather than answering as if it had been consulted:
- Portfolio Risk Lead (exposure, concentration, liquidity, scenario consequences, \
mandate-limit breaches). You cannot assess portfolio weight, sector concentration, \
controlling-group exposure, or exit liquidity for any position yet.
- Market and Event Intelligence Lead (unusual price/volume moves, foreign flow, \
broker activity, filings, corporate events).
- Independent Risk and Evidence Officer. No independent review has been performed \
on any answer from this system. Never describe an answer as "reviewed", "approved", \
"PASS", or similar — those are review-decision labels from a role that does not \
exist in this build, and using them here would misrepresent what actually happened.

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
- You cannot approve your own answer or claim independent review occurred.
- If research surfaces something that would normally also need a portfolio-level or \
risk check (e.g. "should I add this to my portfolio", concentration, liquidity, \
group exposure), answer the research question fully, then state plainly that this \
system cannot yet assess the portfolio-risk side rather than omitting that caveat \
or guessing at it yourself.
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
    """Builds the Chief with Investment Research Lead wired in as an agent-as-tool,
    plus session (Valkey, short-term) and memory (Postgres, long-term) attached.

    Both roles currently share the same model_entry — per-role model tiering
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

    return Agent(
        name=AGENT_ID,
        agent_id=AGENT_ID,
        model=build_model(model_entry),
        tools=[
            investment_research_lead.as_tool(
                name=investment_research_lead.name,
                description=investment_research_lead.description,
            )
        ],
        system_prompt=SYSTEM_PROMPT,
        session_manager=session_manager,
        plugins=[memory_manager],
    )
