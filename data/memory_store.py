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
text-search store (Postgres ILIKE, matching data/db.py::search_user_memory), not a
semantic/embedding one. That keeps it at zero additional API cost, consistent with
this project's credit-consciousness; a real embedding-backed upgrade (pgvector is
already provisioned) is a separate, clearly separable improvement if fuzzy/semantic
recall is ever needed.

Automatic background extraction (MemoryStoreConfig's `extraction` field, which
would run a model call every few turns to auto-distill facts) is deliberately left
off here for the same reason — the Chief has an explicit `add_memory` tool instead,
so remembering something costs nothing unless the model (or the user) actually asks
for it.
"""

from strands.memory.types import MemoryEntry

from data.db import Database


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
            "investment theses, stated preferences. Not for facts about a company "
            "or the market in general — those come from the research tools."
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
        self.db.add_user_memory(self.user_id, content, metadata)
