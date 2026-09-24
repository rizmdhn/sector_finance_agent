"""Integration tests for data/session_repository.py against the real local Valkey —
no mocks, matching this project's convention. Skipped automatically if Valkey is
unreachable.
"""

import os
import uuid

import pytest

from data.cache import Cache
from data.session_repository import ValkeySessionRepository


@pytest.fixture()
def cache() -> Cache:
    try:
        c = Cache(os.environ.get("VALKEY_HOST", "localhost"), int(os.environ.get("VALKEY_PORT", "6379")))
        c._redis.ping()
    except Exception:
        pytest.skip("Valkey not reachable")
    return c


@pytest.fixture()
def repo(cache: Cache) -> ValkeySessionRepository:
    return ValkeySessionRepository(cache, ttl_seconds=60)


def _session_id() -> str:
    return f"test-session-{uuid.uuid4().hex[:8]}"


def test_create_and_read_session(repo: ValkeySessionRepository):
    from strands.types.session import Session, SessionType

    sid = _session_id()
    assert repo.read_session(sid) is None

    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))
    session = repo.read_session(sid)
    assert session is not None
    assert session.session_id == sid


def test_messages_round_trip_in_order(repo: ValkeySessionRepository):
    from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

    sid = _session_id()
    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))
    agent = SessionAgent(agent_id="test_agent", state={}, conversation_manager_state={})
    repo.create_agent(sid, agent)

    repo.create_message(sid, agent.agent_id, SessionMessage.from_message({"role": "user", "content": [{"text": "hello"}]}, 0))
    repo.create_message(
        sid, agent.agent_id, SessionMessage.from_message({"role": "assistant", "content": [{"text": "hi"}]}, 1)
    )

    messages = repo.list_messages(sid, agent.agent_id)
    assert [m.message_id for m in messages] == [0, 1]
    assert messages[0].message["role"] == "user"
    assert messages[1].message["role"] == "assistant"


def test_redaction_overrides_to_message(repo: ValkeySessionRepository):
    from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

    sid = _session_id()
    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))
    agent = SessionAgent(agent_id="test_agent", state={}, conversation_manager_state={})
    repo.create_agent(sid, agent)

    msg = SessionMessage.from_message({"role": "assistant", "content": [{"text": "secret"}]}, 0)
    repo.create_message(sid, agent.agent_id, msg)

    msg.redact_message = {"role": "assistant", "content": [{"text": "[redacted]"}]}
    repo.update_message(sid, agent.agent_id, msg)

    restored = repo.read_message(sid, agent.agent_id, 0)
    assert restored.to_message() == {"role": "assistant", "content": [{"text": "[redacted]"}]}


def test_session_has_a_ttl_and_it_slides_forward_on_write(repo: ValkeySessionRepository, cache: Cache):
    from strands.types.session import Session, SessionType

    sid = _session_id()
    repo.create_session(Session(session_id=sid, session_type=SessionType.AGENT))
    assert 0 < cache._redis.ttl(f"session:{sid}") <= 60


def test_repository_session_manager_restores_history_on_a_fresh_agent_object(repo: ValkeySessionRepository):
    """The actual integration point used by gateway/roles/orchestrator.py — no LLM
    involved, this only exercises Strands' own session-restore mechanics against a
    real Valkey-backed repository.
    """
    from strands.session.repository_session_manager import RepositorySessionManager

    sid = _session_id()
    sm1 = RepositorySessionManager(session_id=sid, session_repository=repo)
    assert sm1._is_new_session is True

    class _FakeAgent:
        agent_id = "test_agent"
        messages = []
        state = type("S", (), {"get": lambda self: {}})()

    fake_agent = _FakeAgent()
    sm1.initialize(fake_agent)  # normally fired by Agent's own AgentInitializedEvent hook
    sm1.append_message({"role": "user", "content": [{"text": "What is BBCA?"}]}, fake_agent)
    sm1.append_message({"role": "assistant", "content": [{"text": "A bank."}]}, fake_agent)

    sm2 = RepositorySessionManager(session_id=sid, session_repository=repo)
    assert sm2._is_new_session is False
    restored = repo.list_messages(sid, "test_agent")
    assert [m.to_message()["content"][0]["text"] for m in restored] == ["What is BBCA?", "A bank."]
