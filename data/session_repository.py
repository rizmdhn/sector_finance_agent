"""Short-term, per-session conversation memory — implements Strands' abstract
`SessionRepository` (`strands.session.session_repository.SessionRepository`),
backed by Valkey with a sliding TTL, so it lives alongside this project's other
Valkey-backed state (data/cache.py) rather than duplicating a second Redis client.

Deliberately Valkey, not Postgres: this is meant to be short-term and ephemeral by
design (an idle conversation should eventually vanish), the opposite of
data/memory_store.py's long-term per-user facts, which must never be silently
evicted. Valkey already runs with `--maxmemory-policy allkeys-lru`
(docker-compose.yml) — correct here, since a session that's actually falling out of
the TTL window is exactly the kind of thing that's fine to lose.

Wired into gateway/roles/orchestrator.py via Strands' `RepositorySessionManager`,
which restores an agent's prior conversation into a freshly-constructed `Agent`
object on `AgentInitializedEvent` and persists new messages as they're added — see
that module and gateway/main.py for how a request decides whether to send its full
message history (new session) or just the newest turn (existing session, since the
rest is restored server-side).
"""

from strands.session.session_repository import SessionRepository
from strands.types.session import Session, SessionAgent, SessionMessage

from data.cache import Cache

DEFAULT_SESSION_TTL_SECONDS = 4 * 60 * 60  # 4 hours of inactivity — "short-term"


class ValkeySessionRepository(SessionRepository):
    def __init__(self, cache: Cache, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS):
        self.cache = cache
        self.ttl_seconds = ttl_seconds

    def _session_key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _agent_key(self, session_id: str, agent_id: str) -> str:
        return f"session:{session_id}:agent:{agent_id}"

    def _messages_key(self, session_id: str, agent_id: str) -> str:
        return f"session:{session_id}:agent:{agent_id}:messages"

    def _touch(self, *keys: str) -> None:
        """Refresh the TTL on every write, so an active session's expiry keeps
        sliding forward instead of counting down from creation."""
        for key in keys:
            self.cache.expire(key, self.ttl_seconds)

    # -- Session ---------------------------------------------------------------

    def create_session(self, session: Session, **kwargs) -> Session:
        key = self._session_key(session.session_id)
        self.cache.set(key, session.to_dict(), ttl=self.ttl_seconds)
        return session

    def read_session(self, session_id: str, **kwargs) -> Session | None:
        data = self.cache.get(self._session_key(session_id))
        return None if data is None else Session.from_dict(data)

    # -- Agent -------------------------------------------------------------------

    def create_agent(self, session_id: str, session_agent: SessionAgent, **kwargs) -> None:
        key = self._agent_key(session_id, session_agent.agent_id)
        self.cache.set(key, session_agent.to_dict(), ttl=self.ttl_seconds)

    def read_agent(self, session_id: str, agent_id: str, **kwargs) -> SessionAgent | None:
        data = self.cache.get(self._agent_key(session_id, agent_id))
        return None if data is None else SessionAgent.from_dict(data)

    def update_agent(self, session_id: str, session_agent: SessionAgent, **kwargs) -> None:
        key = self._agent_key(session_id, session_agent.agent_id)
        self.cache.set(key, session_agent.to_dict(), ttl=self.ttl_seconds)

    # -- Messages ------------------------------------------------------------

    def create_message(self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs) -> None:
        key = self._messages_key(session_id, agent_id)
        self.cache.hset_json(key, str(session_message.message_id), session_message.to_dict())
        self._touch(self._session_key(session_id), self._agent_key(session_id, agent_id), key)

    def read_message(self, session_id: str, agent_id: str, message_id: int, **kwargs) -> SessionMessage | None:
        data = self.cache.hget_json(self._messages_key(session_id, agent_id), str(message_id))
        return None if data is None else SessionMessage.from_dict(data)

    def update_message(self, session_id: str, agent_id: str, session_message: SessionMessage, **kwargs) -> None:
        key = self._messages_key(session_id, agent_id)
        self.cache.hset_json(key, str(session_message.message_id), session_message.to_dict())
        self._touch(key)

    def list_messages(
        self, session_id: str, agent_id: str, limit: int | None = None, offset: int = 0, **kwargs
    ) -> list[SessionMessage]:
        all_messages = self.cache.hgetall_json(self._messages_key(session_id, agent_id))
        ordered = sorted(all_messages.values(), key=lambda m: m["message_id"])
        sliced = ordered[offset:] if limit is None else ordered[offset : offset + limit]
        return [SessionMessage.from_dict(m) for m in sliced]
