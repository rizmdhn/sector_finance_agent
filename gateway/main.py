"""FastAPI gateway exposing an OpenAI-compatible API in front of a Strands agent.

See idx_agent_infrastructure_diagrams_md.md sections 2-4.
"""

import hmac
import os
import secrets
import uuid

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from starlette.background import BackgroundTask

from gateway import compat, memory_extraction
from gateway.agent import build_agent
from gateway.guardrails import (
    RateLimitExceededError,
    attach_disclaimer,
    enforce_rate_limit,
)
from gateway.registry import PLACEHOLDER_MODEL_ID, load_registry
from gateway.roles.orchestrator import DEFAULT_ROLE_TIERS, resolve_role_tiers
from gateway.telemetry import setup_telemetry, traced_conversation
from data.deps import get_cache, get_db
from data.memory_store import write_memory
from data.session_repository import ValkeySessionRepository

SESSION_HEADER = "X-Session-Id"

ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_SESSION_TTL_SECONDS = 7 * 24 * 3600

setup_telemetry()

app = FastAPI(title="IDX Agent Gateway")
_auth_scheme = HTTPBearer()

_registry = load_registry()


@app.on_event("shutdown")
def _flush_traces() -> None:
    """Spans are batched (strands/telemetry's BatchSpanProcessor, default 5s export
    interval) — flush explicitly on shutdown so a request handled just before the
    process exits doesn't lose its trace to timing."""
    from opentelemetry import trace as trace_api

    trace_api.get_tracer_provider().force_flush(timeout_millis=5000)


def _check_auth(credentials: HTTPAuthorizationCredentials = Depends(_auth_scheme)) -> None:
    expected = os.environ["IDX_GATEWAY_KEY"]
    if credentials.credentials != expected:
        raise HTTPException(status_code=401, detail="invalid gateway key")


class LoginRequest(BaseModel):
    password: str


@app.post("/v1/auth/login")
def login(body: LoginRequest, response: Response) -> dict:
    """Session-cookie login for a human at admin-ui — separate from _check_auth's
    shared bearer key, which authenticates *callers* (admin-ui's own nginx,
    LibreChat), not a person: anyone who loaded admin-ui previously got full
    access with no login at all, since nginx injected that key for every request
    regardless of who was looking at the page. One admin password, not a users
    table — this panel has exactly one operator; a real accounts system is a
    bigger, separate feature to build if that changes (see PROGRESS.md).
    """
    try:
        enforce_rate_limit(get_cache(), "admin-login", limit_per_minute=5)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    if not hmac.compare_digest(body.password, os.environ["ADMIN_PASSWORD"]):
        raise HTTPException(status_code=401, detail="wrong password")

    token = secrets.token_urlsafe(32)
    get_cache().set(f"admin_session:{token}", True, ttl=ADMIN_SESSION_TTL_SECONDS)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        max_age=ADMIN_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        path="/",
        # Not `secure=True`: nothing in this repo terminates TLS yet (see
        # docker-compose.yml) — flip this once a real deployment sits behind HTTPS.
    )
    return {"ok": True}


@app.post("/v1/auth/logout")
def logout(response: Response, admin_session: str | None = Cookie(default=None)) -> dict:
    if admin_session:
        get_cache().delete(f"admin_session:{admin_session}")
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/v1/auth/verify")
def verify_session(admin_session: str | None = Cookie(default=None)) -> dict:
    """No `_check_auth` dependency — this endpoint IS the auth check. Used two
    ways: by nginx's `auth_request` (admin-ui/nginx.conf.template) to gate every
    `/api/` request before proxying it anywhere, and directly by the frontend on
    load to ask "am I already logged in"."""
    if not admin_session or get_cache().get(f"admin_session:{admin_session}") is None:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"ok": True}


@app.get("/v1/models", dependencies=[Depends(_check_auth)])
async def list_models() -> dict:
    """Return the model names LibreChat can select from. Registry entries with a
    placeholder model_id (models.yaml's still-TODO `standard`/`strong` slots — see
    that file) are excluded: they exist only so gateway/registry.py's
    select_for_tier can find them for per-role tiering, and would error if a user
    picked one directly as the top-level model."""
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "idx-agent"}
            for name, entry in _registry.items()
            if entry.model_id != PLACEHOLDER_MODEL_ID
        ],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(_check_auth)])
async def chat_completions(request: Request):
    """Look up the requested model in the registry, build a Strands Agent,
    run it against the incoming messages, and stream back OpenAI-format
    SSE chunks (or a single JSON body when `stream` is false).
    """
    body = await request.json()
    model_name = body["model"]
    messages = body["messages"]
    stream = body.get("stream", False)
    user_id = body.get("user", "anonymous")

    if model_name not in _registry:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_name}")

    if compat.is_title_request(messages):
        return _short_circuit_title_response(model_name)

    try:
        enforce_rate_limit(get_cache(), user_id)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    model_entry = _registry[model_name]
    system_prompt, chat_messages = compat.split_system_prompt(messages)
    question = compat.latest_user_text(chat_messages)

    # A session the client echoes back already has its history restored
    # server-side (gateway/roles/orchestrator.py's session_manager) — resending
    # the full array would duplicate it, so only the newest turn goes to the
    # agent. No session header, or one Valkey has never seen (expired or never
    # existed), gets the client's full array once to seed it — see
    # data/session_repository.py's TTL for how long a session stays alive.
    session_id = request.headers.get(SESSION_HEADER) or uuid.uuid4().hex
    is_continuing_session = ValkeySessionRepository(get_cache()).read_session(session_id) is not None
    strands_messages = compat.to_strands_messages(chat_messages[-1:] if is_continuing_session else chat_messages)

    agent = _build_agent_with_fallback(model_entry, user_id=user_id, session_id=session_id)
    if system_prompt:
        agent.system_prompt = f"{agent.system_prompt}\n\n{system_prompt}"

    if stream:
        # `result` is filled by _stream_response as the generator runs, but the
        # BackgroundTask below is only *constructed* now, before any of that has
        # happened — passing the dict itself (not its not-yet-known "answer" key)
        # means the extraction call, which Starlette only runs once the whole
        # streamed body has been sent to the client, sees the real answer.
        result: dict = {}
        response = StreamingResponse(
            _stream_response(agent, strands_messages, model_name, question, user_id, result),
            media_type="text/event-stream",
            background=BackgroundTask(memory_extraction.maybe_extract, get_db(), user_id, session_id, question, result),
        )
        response.headers[SESSION_HEADER] = session_id
        return response

    with traced_conversation(question, model_name, user_id) as span:
        text = await _run_to_completion(agent, strands_messages)
        answer = attach_disclaimer(text)
        span.set_output(answer)
    return JSONResponse(
        compat.completion_response(compat.completion_id(), model_name, answer),
        headers={SESSION_HEADER: session_id},
        background=BackgroundTask(
            memory_extraction.maybe_extract, get_db(), user_id, session_id, question, {"answer": answer}
        ),
    )


def _build_agent_with_fallback(model_entry, *, user_id: str, session_id: str):
    try:
        return build_agent(
            model_entry, db=get_db(), cache=get_cache(), user_id=user_id, session_id=session_id, registry=_registry
        )
    except Exception:
        if not model_entry.fallback:
            raise
        return build_agent(
            _registry[model_entry.fallback],
            db=get_db(),
            cache=get_cache(),
            user_id=user_id,
            session_id=session_id,
            registry=_registry,
        )


async def _run_to_completion(agent, messages: list[dict]) -> str:
    chunks: list[str] = []
    async for event in agent.stream_async(prompt=messages):
        if "data" in event:
            chunks.append(event["data"])
    return "".join(chunks)


async def _stream_response(
    agent, messages: list[dict], model_name: str, question: str, user_id: str, result: dict
):
    """Confirmed live (2026-09-24) that Strands' stream_async() exposes which top-
    level tool the Chief is currently calling via event["current_tool_use"]["name"]
    — for this agent that's always one of the 4 specialists or search_memory/
    add_memory, since those are the Chief's only tools. Surfaced here as a
    non-standard `step` field alongside the normal OpenAI `content` delta field;
    an OpenAI-compatible client (LibreChat) just won't recognize the extra key and
    ignores it, admin-ui's own Chat screen renders it as a live step label. Only
    emitted on an actual change, not every delta — current_tool_use repeats for
    every streamed fragment of the same tool call's arguments.

    Deliberately stops at this level: a specialist's OWN internal tool calls (e.g.
    investment_research_lead calling analyze_fundamentals) are nested inside a
    `tool_stream_event` wrapper and not unpacked here — which specialist is
    being consulted is the meaningful signal to show a user, not which of
    Sectors' internal endpoints it happens to be hitting.
    """
    completion_id_ = compat.completion_id()
    chunks: list[str] = []
    last_step: str | None = None
    with traced_conversation(question, model_name, user_id) as span:
        async for event in agent.stream_async(prompt=messages):
            tool_name = (event.get("current_tool_use") or {}).get("name")
            if tool_name and tool_name != last_step:
                last_step = tool_name
                yield compat.sse_chunk(completion_id_, model_name, {"step": tool_name})
            if "data" in event and event["data"]:
                chunks.append(event["data"])
                yield compat.sse_chunk(completion_id_, model_name, {"content": event["data"]})
        span.set_output("".join(chunks))
    result["answer"] = "".join(chunks)
    yield compat.sse_chunk(completion_id_, model_name, {}, finish_reason="stop")
    yield compat.sse_done()


class AddMemoryRequest(BaseModel):
    user: str
    content: str
    metadata: dict | None = None


class UpdateMemoryRequest(BaseModel):
    user: str
    content: str


@app.get("/v1/memory", dependencies=[Depends(_check_auth)])
def list_memory(user: str, limit: int = 100) -> dict:
    """List a user's long-term memory facts (data/memory_store.py), newest first.
    Real endpoint, not one of the model's own tools — built so a UI can show what's
    remembered about a user without going through a chat turn. Scoped to `user`
    exactly like /v1/chat/completions' `user` field. Protected two ways in
    practice: the shared bearer key here (any caller that has it can read any
    user's memory by naming their id), plus — for admin-ui specifically — nginx's
    `auth_request` gate in front of the whole `/api/` proxy, requiring a real admin
    login (see login()/verify_session() above) before a browser ever reaches this
    far. Still not real per-user auth (one admin, not one login per end user) —
    acceptable for this project's scope (see PROGRESS.md).
    """
    rows = get_db().search_user_memory(user, None, limit)
    return {"data": [_serialize_memory_row(row) for row in rows]}


@app.post("/v1/memory", dependencies=[Depends(_check_auth)])
def add_memory(body: AddMemoryRequest) -> dict:
    """Add a memory fact directly, bypassing the model — lets a UI let the user
    record a fact themselves rather than only through add_memory's tool calls
    mid-chat. Routed through data/memory_store.py's write_memory (not a plain
    insert) so a manually-added fact gets the same embedding/consolidation
    treatment as one the Chief or background extraction wrote, when this user
    has auto_extraction on."""
    row = write_memory(get_db(), body.user, body.content, body.metadata)
    return _serialize_memory_row(row)


class UpdateMemorySettingsRequest(BaseModel):
    user: str
    auto_extraction: bool


@app.get("/v1/memory/settings", dependencies=[Depends(_check_auth)])
def get_memory_settings(user: str) -> dict:
    """Whether this user has AgentCore-style automatic memory extraction turned
    on (gateway/memory_extraction.py) — off by default, see data/schema.sql's
    user_memory_settings table for why. Declared before the
    /v1/memory/{memory_id} routes below: FastAPI matches routes in declaration
    order, and a `PATCH /v1/memory/settings` declared after `PATCH
    /v1/memory/{memory_id}` would have "settings" swallowed as memory_id
    instead (found live: first attempt at this endpoint 422'd, "settings" failing
    int-parsing as a memory_id)."""
    return {"user": user, **get_db().get_memory_settings(user)}


@app.patch("/v1/memory/settings", dependencies=[Depends(_check_auth)])
def update_memory_settings(body: UpdateMemorySettingsRequest) -> dict:
    return {"user": body.user, **get_db().set_memory_settings(body.user, body.auto_extraction)}


@app.patch("/v1/memory/{memory_id}", dependencies=[Depends(_check_auth)])
def update_memory(memory_id: int, body: UpdateMemoryRequest) -> dict:
    """Edit a fact's content in place — same `(id, user)` scoping as delete, so
    one user can't edit another's fact by guessing an id. Doesn't touch `metadata`
    (the `kind` tag stays whatever it was) or `created_at` (this corrects a fact,
    it doesn't re-date it)."""
    row = get_db().update_user_memory(body.user, memory_id, body.content)
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found for this user")
    return _serialize_memory_row(row)


@app.delete("/v1/memory/{memory_id}", dependencies=[Depends(_check_auth)])
def delete_memory(memory_id: int, user: str) -> dict:
    deleted = get_db().delete_user_memory(user, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found for this user")
    return {"deleted": True}


def _serialize_memory_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "metadata": row["metadata"],
        "created_at": row["created_at"].isoformat(),
    }


VALID_TIERS = {"cheap", "standard", "strong"}


class UpdateTierRequest(BaseModel):
    user: str
    tier: str


@app.get("/v1/admin/model-tiers", dependencies=[Depends(_check_auth)])
def get_model_tiers(user: str) -> dict:
    """The real thing admin-ui's Model Tiering page was a mock in front of (see
    PROGRESS.md item 29/36/38) — `config` is THIS user's own live Postgres-backed
    role->tier map (gateway/roles/orchestrator.py's DEFAULT_ROLE_TIERS with their
    role_tier_config overrides applied — every user picks their own tiering, same
    `user` scoping as /v1/memory), `models` is the real registry, not a
    hand-copied list a UI has to keep in sync by hand."""
    return {
        "config": resolve_role_tiers(get_db(), user),
        "models": [
            {
                "name": entry.name,
                "provider": entry.provider,
                "tier": entry.tier,
                "usable": entry.model_id != PLACEHOLDER_MODEL_ID,
            }
            for entry in _registry.values()
        ],
    }


@app.patch("/v1/admin/model-tiers/{role}", dependencies=[Depends(_check_auth)])
def update_model_tier(role: str, body: UpdateTierRequest) -> dict:
    """Takes effect on THIS user's very next request — gateway/roles/
    orchestrator.py's build_agent() reads role_tier_config fresh every time,
    scoped to that request's user_id, no cache, no redeploy."""
    if role not in DEFAULT_ROLE_TIERS:
        raise HTTPException(status_code=404, detail=f"unknown role: {role}")
    if body.tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"invalid tier: {body.tier!r}, must be one of {VALID_TIERS}")
    get_db().set_role_tier(body.user, role, body.tier)
    return {"user": body.user, "role": role, "tier": body.tier}


def _short_circuit_title_response(model_name: str) -> JSONResponse:
    return JSONResponse(
        compat.completion_response(compat.completion_id(), model_name, "IDX Analyst chat")
    )
