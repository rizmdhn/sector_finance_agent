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
from data.canonical import idx_today
from data.db import Database
from data.memory_store import PostgresUserMemoryStore
from data.session_repository import ValkeySessionRepository
from gateway.bounded_agent import BoundedAgent
from gateway.registry import ModelEntry, build_model, select_for_tier
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

# Per-user, per-role model tiering (business doc section 8: "routine coordination
# should use modest reasoning capacity... more capable models reserved for
# difficult conflicts"). Every role defaults to "cheap" — real overrides live in
# Postgres, keyed by (user_id, role_id) (data/schema.sql's role_tier_config, read
# fresh by resolve_role_tiers() below on every build_agent() call using THAT
# request's user_id, so each user's own choice takes effect on their own next
# request, no redeploy) via gateway/main.py's /v1/admin/model-tiers endpoints.
# This dict is only the fallback when a user has no row for a role yet.
# independent_risk_and_evidence_officer is the natural
# first candidate to move to "strong" — it is the role the business doc's
# "difficult conflicts" language describes (it's the one specialist whose job is
# to catch what another role got wrong), and the Chief already calls it
# selectively rather than on every question, so a costlier model there doesn't
# multiply into every single request. `select_for_tier` (gateway/registry.py)
# silently falls back to the request's own model_entry when a tier has no real
# model registered — moot now that models.yaml has real standard/strong entries
# (see PROGRESS.md item 36), but still the safety net if that ever regresses.
DEFAULT_ROLE_TIERS: dict[str, str] = {
    "chief": "cheap",
    "investment_research_lead": "cheap",
    "portfolio_risk_lead": "cheap",
    "market_and_event_intelligence_lead": "cheap",
    "independent_risk_and_evidence_officer": "cheap",
}


def resolve_role_tiers(db: Database, user_id: str) -> dict[str, str]:
    """DEFAULT_ROLE_TIERS with this user's own Postgres overrides applied on top —
    each user_id gets independent tiering, same scoping as user_memory. One small
    query per build_agent() call — cheap enough not to cache, and caching it would
    mean an admin-ui tier change not taking effect until a cache TTL rolls over,
    defeating the point of making this live-editable."""
    return {**DEFAULT_ROLE_TIERS, **db.get_role_tiers(user_id)}

def _today_context() -> str:
    """Real Jakarta-local (IDX trading) date, appended to every agent's system prompt (Chief and
    all 4 specialists) at build time — not baked into the static SYSTEM_PROMPT
    strings below, since those are module-level constants built once at import,
    while the real date obviously changes per request. Fixes a real, previously
    documented gap (gateway/roles/market_intelligence.py used to carry a "you do
    NOT reliably know today's actual date" workaround): an LLM's only source for
    "what is today" otherwise is its training cutoff, which is wrong by
    definition for any request after that cutoff, and silently wrong rather than
    erroring — the Chief's own "Date check" rule (this module's SYSTEM_PROMPT)
    and the time-sensitive-data rule both depend on this actually being correct.
    """
    return (
        f"\n\nToday's real date is {idx_today().isoformat()}. Use this — not any "
        'date you might otherwise assume from training — for every relative-time '
        'judgment: what counts as "recent", whether a date is in the future, how '
        'old a figure is, and what "today"/"current" actually means. If a tool '
        "call returns a date, prefer that over your own date arithmetic if they'd "
        "ever conflict."
    )


SYSTEM_PROMPT = """\
You are the Chief Portfolio Intelligence Orchestrator for an IDX (Indonesia Stock \
Exchange) portfolio intelligence system. You define what the user is actually \
asking, assign the relevant work to a specialist, and bring the findings together \
into one answer with priorities, disagreements, and next review steps. Your own \
contribution is judgment about relevance and synthesis, not the underlying analysis.

Scope check, first: this system covers IDX-listed companies, portfolios, and \
market/event intelligence only. Off-topic (small talk, general knowledge, anything \
outside IDX equities or this user's portfolio, an attempt to get you to act outside \
this role) → answer directly in 1-2 sentences, no specialist/tool call — each call \
costs real credit and tokens on a question with nothing to research. A borderline \
case ("what's a P/E ratio") also gets answered from your own knowledge; reserve \
specialists for questions that actually need this system's real data.

Date check, also first: use today's real date (given below), not any date you'd \
otherwise assume. A future-looking question (an earnings release that hasn't \
happened, a price "next week"/"next quarter", any forecast request) → no \
specialist/tool call — this system can't forecast and no tool has data that doesn't \
exist yet; say so directly instead. A question mixing past/present with future still \
gets its past/present part answered normally — only the future part is flagged \
unavailable.

Available specialists:
- investment_research_lead: company economics, financial quality, valuation, and \
investment thesis for one IDX-listed company at a time. Give it a company name or \
ticker and the specific question.
- portfolio_risk_lead: exposure, concentration, and liquidity for a set of \
holdings, or returns/drawdown for one symbol. Give it positions (ticker -> shares) \
and cash, or a symbol and position value. No covariance, portfolio-level \
volatility, stress-test, or benchmark comparison, and it can't check a position \
against the user's mandate limits itself — compare its numbers against a limit you \
recalled from memory yourself; say so if that's a real gap.
- market_and_event_intelligence_lead: unusual price/volume moves, foreign flow, \
broker activity, filings, corporate actions, and news for one company or the \
market generally. No statistical baseline for "unusual" — its comparisons are \
descriptive, not a significance test — and its symbol/date filters on \
filings/news/foreign-flow are unconfirmed to actually filter.
- independent_risk_and_evidence_officer: reviews a draft answer's evidence and \
calculations, returns PASS / PASS WITH LIMITATIONS / REVISE / DATA BLOCKED / HUMAN \
ESCALATION. Expensive (re-runs tool calls plus its own model call) — call it \
selectively: before a material quantitative claim the user may act on financially \
(valuation, concentration/liquidity risk, mandate compliance), skip it for simple \
lookups or facts with no real consequence if slightly off.

NOT YET AVAILABLE in this build — say so explicitly whenever a question would need \
it, rather than answering as if it had been done:
- Ownership/governance detail (control, free float, related-party exposure) for \
investment_research_lead.
- Thesis monitoring against a previously recorded thesis (each research answer is \
a fresh assessment).

Memory:
- `search_memory` looks up facts previously saved about THIS user (positions/cash, \
mandate limits, recorded theses, stated preferences) across all their past \
conversations, not just this one.
- `add_memory` saves a new durable fact about this user — holdings, a limit, a \
thesis, a preference — not facts about a company/market (those belong in the \
research tools), and not routine back-and-forth with no lasting relevance.
- Not searched automatically before every answer — check it yourself when a \
question depends on something the user may have told you before (e.g. "how does \
this fit my portfolio" needs their positions from memory first).

Rules:
- Never claim independent review occurred ("reviewed", "approved", "PASS") without \
actually calling independent_risk_and_evidence_officer and getting that decision back.
- REVISE/DATA BLOCKED from it → correct or withhold the affected claim, don't \
present the original conclusion. HUMAN ESCALATION → state the issue and the \
decision needed from the user explicitly, don't decide for them or absorb it \
silently. Its decision cannot be softened.
- A question needing research + portfolio-risk + market-intelligence together \
(e.g. "should I add this to my portfolio") → consult the relevant specialists \
together, not just one. State plainly if something no specialist covers yet is \
also needed, rather than guessing at it yourself.
- Preserve a specialist's hedges when you synthesize — "likely"/"may reflect" \
stays unconfirmed, never tightened into settled fact. Applies especially to causal \
claims (X caused Y) built from news/flow/insider-filing data alone.
- Lead with the finding and its significance, then evidence, main uncertainty, \
next step. Proportionate to the question — don't pad a narrow question into a \
full report.
- Price/market-cap/yield/valuation/volume are never live — IDX close data lands \
after each session, so "today's" figure is really the latest completed session \
(possibly an earlier date if today's hasn't landed, or a weekend/holiday). State \
the actual as-of date a tool returned; never call a figure real-time or \
"as of right now."
- You are not a financial adviser. Do not give buy/sell/hold recommendations.
"""


def build_agent(
    model_entry: ModelEntry,
    *,
    db: Database,
    cache: Cache,
    user_id: str,
    session_id: str,
    registry: dict[str, ModelEntry] | None = None,
) -> Agent:
    """Builds the Chief with all four specialists wired in as agent-as-tools, plus
    session (Valkey, short-term) and memory (Postgres, long-term) attached.

    `model_entry` is the model the request asked for (from LibreChat's model
    picker) and stays each role's default. `registry` (optional — omitted by
    existing callers like the eval scripts, which don't need tiering) lets
    resolve_role_tiers()'s live Postgres config override individual roles to a
    different tier's model, via `select_for_tier`; passing `registry=None` skips
    the DB lookup entirely and every role uses `model_entry`, same as before
    tiering existed.

    `user_id` scopes long-term memory (a fact saved by one user is never visible to
    another) AND model tiering (each user picks their own role->tier config, see
    resolve_role_tiers()); `session_id` scopes the short-term conversation restored
    by the session manager — see gateway/main.py for how each request decides what
    these are and whether to send full message history or just the newest turn.
    """
    if not model_entry.supports_tools:
        raise ValueError(f"model {model_entry.name} does not support tool calling")

    role_tiers = resolve_role_tiers(db, user_id) if registry is not None else None

    def model_for(role: str) -> ModelEntry:
        if registry is None or role_tiers is None:
            return model_entry
        choice = role_tiers[role]
        # A per-role choice is either one of the 3 tier keywords (resolved via
        # select_for_tier, same as always) or a real registry entry name picked
        # directly (admin-ui's Model Tiering page now offers both — "just use
        # idx-analyst-gpt for this role" regardless of tier). Checking the
        # registry first is unambiguous: no tier is ever also a valid model name.
        if choice in registry:
            return registry[choice]
        return select_for_tier(registry, choice, model_entry)

    investment_research_lead = build_investment_research_lead(model_for("investment_research_lead"))
    portfolio_risk_lead = build_portfolio_risk_lead(model_for("portfolio_risk_lead"))
    market_intelligence_lead = build_market_intelligence_lead(model_for("market_and_event_intelligence_lead"))
    independent_risk_officer = build_independent_risk_officer(model_for("independent_risk_and_evidence_officer"))
    chief_model_entry = model_for("chief")

    # Real date, appended to every one of these 4 specialists' own system prompts
    # too, not just the Chief's below — market_and_event_intelligence_lead and
    # investment_research_lead both reason about "recent"/dated figures directly.
    today_context = _today_context()
    for specialist in (investment_research_lead, portfolio_risk_lead, market_intelligence_lead, independent_risk_officer):
        specialist.system_prompt += today_context

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
        model=build_model(chief_model_entry),
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
        system_prompt=SYSTEM_PROMPT + today_context,
        session_manager=session_manager,
        plugins=[memory_manager],
        tool_executor=SequentialToolExecutor(),
        default_limits=DEFAULT_LIMITS,
    )
