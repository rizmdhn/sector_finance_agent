"""Long-term, per-user memory — implements Strands' `MemoryStore` protocol
(`strands.memory.types.MemoryStore`) so the Chief gets Strands' own `add_memory`/
`search_memory` tools for free, rather than hand-rolled ones.

Backed by Postgres (`user_memory` table, data/schema.sql), not Valkey — durable by
design, unlike the short-term session store (data/session_repository.py). Valkey
runs with `--maxmemory-policy allkeys-lru` in this project's docker-compose.yml,
which is correct for a cache (a dropped key just gets refetched) but wrong for
memory that's supposed to persist: a user's portfolio or a recorded thesis quietly
disappearing under memory pressure would be a real correctness problem, not just a
slowdown.

Deliberately free-form (a fact is a string, not a set of typed columns) per the
user's explicit "anything user related should be configurable" — this is a plain
text-search store (Postgres full-text search, matching data/db.py::search_user_memory),
not a semantic/embedding one. That keeps it at zero additional API cost, consistent
with this project's credit-consciousness; a real embedding-backed upgrade (pgvector is
already provisioned) is a separate, clearly separable improvement if fuzzy/semantic
recall is ever needed.

Predefined categories, auto-classified: the business doc's four standing categories
(portfolio, mandate_limit, thesis, preference) are kept as a `kind` tag in metadata,
but the model itself can't set it — Strands' generic `add_memory` tool (built by
`MemoryManager`) only exposes `entries: list[str]`, with no per-entry metadata
parameter for the model to fill in. So `_classify()` below tags each fact
server-side, by keyword, at write time. This is purely organizational (for future
filtering/analytics on the `metadata->>'kind'` column) — it never gates what gets
written or recalled; a fact that matches none of the four predefined keywords is
tagged "other" and stored and searched exactly like any other fact. That's the
"predefined categories from us, but still anything user-related is configurable"
split: the taxonomy is ours, the content is the user's.

Automatic background extraction is a per-user OPT-IN now (AgentCore-parity, see
PROGRESS.md): off by default, same reasoning as above — the Chief's own
`add_memory` tool already lets remembering something cost nothing unless someone
actually asks for it. A user who wants AgentCore's actual behavior (facts pulled
out of every conversation automatically, LLM-judged dedup/consolidation instead
of plain inserts) can turn it on via `/v1/memory/settings` (data/db.py's
get_memory_settings/set_memory_settings) — see `write_memory`/`write_summary`
below for what changes when it's on, and gateway/memory_extraction.py for the
background pass that calls them.

Runs entirely on Anthropic, deliberately — this project only holds an Anthropic
key, and Anthropic has no embeddings API, so "consolidation" here means the
same full-text search `search()` uses to find candidate facts, judged by a
cheap Anthropic call (data/memory_llm.py::decide_consolidation) rather than
cosine-distance over embeddings. Real semantic (embedding) recall was tried
first and reverted — see PROGRESS.md item 41 for why.
"""

from strands.memory.types import MemoryEntry

from data.db import Database
from data.memory_llm import decide_consolidation

# The business doc's four standing memory categories (section 4), kept as a
# best-effort `kind` tag for organization — see module docstring for why this is
# classified server-side rather than set by the model.
PREDEFINED_KINDS = ("portfolio", "mandate_limit", "thesis", "preference")
OTHER_KIND = "other"

_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mandate_limit": (
        "mandate", "limit", "concentration", "cap", "max exposure", "risk tolerance",
        "exposure limit", "max allocation", "not allowed to", "must not exceed",
    ),
    "portfolio": (
        "shares of", "holds", "holding", "position", "portfolio", "watchlist",
        "bought", "sold", "owns", "cash balance", "allocated",
    ),
    "thesis": (
        "thesis", "bull case", "bear case", "target price", "conviction",
        "expect", "believe", "reason for buying", "reason for holding",
    ),
    "preference": (
        "prefer", "preference", "like to", "dislike", "always", "never",
        "style", "format", "want to see", "don't want",
    ),
}


def _classify(content: str) -> str:
    """Best-effort `kind` tag for a fact, by keyword — see module docstring.

    Order matters where keyword sets could both match (e.g. "concentration limit
    on my portfolio" is a mandate, not a plain portfolio fact): mandate_limit is
    checked first, since its keywords are the most specific of the four.
    """
    lowered = content.lower()
    for kind in ("mandate_limit", "portfolio", "thesis", "preference"):
        if any(keyword in lowered for keyword in _KIND_KEYWORDS[kind]):
            return kind
    return OTHER_KIND


def write_memory(db: Database, user_id: str, content: str, metadata: dict | None = None) -> dict:
    """Single write path for a new memory fact — used by the Chief's add_memory
    tool (PostgresUserMemoryStore.add below), POST /v1/memory (gateway/main.py),
    and background extraction (gateway/memory_extraction.py) alike, so
    consolidation behaves identically no matter who triggered the write.

    LLM-based consolidation (AgentCore's ADD/UPDATE/SKIP) only runs when this
    user has turned on auto_extraction — a real Anthropic API call this
    project's credit-consciousness says should be opt-in, not automatic for
    every write. Candidates to compare against come from the same full-text
    search `search()` below already uses (not embeddings — this project runs
    entirely on Anthropic, which has no embeddings API; see this module's
    top-of-file docstring). Degrades to today's plain insert when the toggle
    is off.
    """
    tagged = {"kind": _classify(content), **(metadata or {})}
    if not db.get_memory_settings(user_id)["auto_extraction"]:
        return db.add_user_memory(user_id, content, tagged)

    candidates = db.search_user_memory(user_id, content, limit=3)
    decision = decide_consolidation(content, candidates) if candidates else {"action": "ADD"}

    if decision.get("action") == "SKIP":
        return candidates[0]
    if decision.get("action") == "UPDATE" and decision.get("target_id"):
        db.invalidate_user_memory(user_id, decision["target_id"])
        tagged["supersedes"] = decision["target_id"]
    return db.add_user_memory(user_id, content, tagged)


def write_summary(db: Database, user_id: str, session_id: str, summary: str) -> dict:
    """A session's running summary is upserted wholesale, not consolidated
    fact-by-fact like write_memory's semantic/preference facts — there's exactly
    one active summary per session, replaced on each background extraction pass
    (gateway/memory_extraction.py)."""
    existing = db.get_summary_for_session(user_id, session_id)
    if existing:
        return db.update_user_memory(user_id, existing["id"], summary)
    return db.add_user_memory(user_id, summary, {"kind": "summary", "session_id": session_id})


class PostgresUserMemoryStore:
    """One instance per request, scoped to a single user_id — the gateway builds a
    fresh one per `build_agent()` call, matching how Agents are already built fresh
    per request in this codebase (see gateway/roles/orchestrator.py).
    """

    def __init__(self, db: Database, user_id: str, max_search_results: int = 20):
        self.db = db
        self.user_id = user_id
        self.name = "user_memory"
        self.description = (
            "Facts about this user that should persist across conversations: "
            "portfolio/watchlist positions and cash, mandate limits, recorded "
            "investment theses, stated preferences — or anything else about this "
            "user worth remembering long-term, not limited to those examples. Not "
            "for facts about a company or the market in general — those come from "
            "the research tools."
        )
        self.max_search_results = max_search_results
        self.writable = True
        self.extraction = False

    async def search(self, query: str, options: dict | None = None) -> list[MemoryEntry]:
        limit = (options or {}).get("max_search_results", self.max_search_results)
        rows = self.db.search_user_memory(self.user_id, query, limit)
        return [
            MemoryEntry(
                content=row["content"],
                metadata={**(row["metadata"] or {}), "created_at": str(row["created_at"])},
            )
            for row in rows
        ]

    async def add(self, content: str, metadata: dict | None = None) -> None:
        write_memory(self.db, self.user_id, content, metadata)
