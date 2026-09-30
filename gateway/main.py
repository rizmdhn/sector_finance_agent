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

from gateway import compat, eval_runner, memory_extraction
from gateway.agent import build_agent
from gateway.guardrails import (
    RateLimitExceededError,
    attach_disclaimer,
    enforce_rate_limit,
)
from gateway.registry import PLACEHOLDER_MODEL_ID, has_usable_key, load_registry
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


def _validate_startup_config() -> None:
    """Fail fast with one clear message instead of a confusing KeyError/500 deep
    in a request handler — the exact failure mode hit live (2026-10-01) on a fresh
    clone missing SECTORS_API_KEY, and separately when no model provider key was
    configured at all. Also applies data/schema.sql (idempotent, CREATE TABLE IF
    NOT EXISTS throughout) so a fresh database doesn't need the README's manual
    `init-db` step either.
    """
    missing = [key for key in ("SECTORS_API_KEY", "IDX_GATEWAY_KEY") if not os.environ.get(key)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set them in your .env file before starting the gateway."
        )
    if not any(has_usable_key(entry) for entry in _registry.values()):
        raise RuntimeError(
            "No usable model API key configured — set ANTHROPIC_API_KEY and/or "
            "OPENAI_API_KEY in your .env file."
        )
    get_db().init_schema()


_validate_startup_config()


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
    """Walks the FULL fallback chain, not just one hop — a single retry isn't
    enough when a chain has more than one entry on the same now-unusable provider
    (e.g. idx-analyst-claude-sonnet -> idx-analyst-claude, both Anthropic: if the
    real failure is a missing/blank ANTHROPIC_API_KEY, that first fallback fails
    for the identical reason, and only a second hop to a GPT entry actually
    recovers). `seen` guards against a cycle in a hand-edited models.yaml turning
    this into an infinite loop — fails loudly on a cycle rather than hanging.
    """
    seen: set[str] = set()
    entry = model_entry
    while True:
        try:
            return build_agent(
                entry, db=get_db(), cache=get_cache(), user_id=user_id, session_id=session_id, registry=_registry
            )
        except Exception:
            seen.add(entry.name)
            if not entry.fallback or entry.fallback in seen:
                raise
            entry = _registry[entry.fallback]


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
    # Either one of VALID_TIERS (resolved to a model via
    # gateway/registry.py::select_for_tier, same as always) or a real
    # models.yaml entry `name` picked directly (gateway/roles/orchestrator.py's
    # model_for() checks the registry before falling back to tier resolution) —
    # admin-ui's Model Tiering page offers both in one dropdown now.
    tier: str


@app.get("/v1/admin/readiness", dependencies=[Depends(_check_auth)])
def get_readiness() -> dict:
    """Lets admin-ui gate the real UI behind a "still setting up" screen instead of
    letting a user hit a confusing "unknown symbol" error on a fresh deploy whose
    ingest-worker hasn't finished its one-time seed yet (ingest/scheduler.py
    bootstraps symbol_master automatically on an empty database).

    `price_data_ready` is reported but NOT required for `ready` — checked live
    (2026-10-01): every price-dependent tool (analysis_bridge's portfolio/
    liquidity/returns snapshots) already degrades gracefully to UNAVAILABLE /
    missing_price_symbols when price_daily is empty, by the same design this
    whole project uses for any missing data, rather than erroring. Company
    reports, ownership, fundamentals and the screener don't touch price_daily at
    all. Only symbol_master (every ticker lookup hard-fails without it) and a
    usable model key are real blockers — gating on price data too would hold the
    UI back over something the rest of the system already handles gracefully.
    """
    db = get_db()
    symbol_master_ready = db.has_symbol_master()
    price_data_ready = db.latest_trade_date() is not None
    model_key_ready = any(has_usable_key(entry) for entry in _registry.values())
    return {
        "ready": symbol_master_ready and model_key_ready,
        "symbol_master_ready": symbol_master_ready,
        "price_data_ready": price_data_ready,
        "model_key_ready": model_key_ready,
    }


@app.get("/v1/admin/model-tiers", dependencies=[Depends(_check_auth)])
def get_model_tiers(user: str) -> dict:
    """The real thing admin-ui's Model Tiering page was a mock in front of (see
    PROGRESS.md item 29/36/38) — `config` is THIS user's own live Postgres-backed
    role->tier map (gateway/roles/orchestrator.py's DEFAULT_ROLE_TIERS with their
    role_tier_config overrides applied — every user picks their own tiering, same
    `user` scoping as /v1/memory), `models` is the real registry, not a
    hand-copied list a UI has to keep in sync by hand.

    `key_configured` added 2026-09-30 — real bug found live: the frontend's own
    "resolved model" preview only checked `usable` (not a models.yaml placeholder),
    with zero awareness of which provider actually has an API key set. It
    confidently showed a green "live" dot on idx-analyst-claude with only an
    OpenAI key configured — the backend (gateway/registry.py's select_for_tier,
    has_usable_key) had already been fixed to route around that correctly, but
    the UI kept claiming something different was about to run. This exposes the
    same signal the backend actually uses, so the preview can finally tell the
    truth instead of a plausible-looking guess.
    """
    return {
        "config": resolve_role_tiers(get_db(), user),
        "models": [
            {
                "name": entry.name,
                "provider": entry.provider,
                "tier": entry.tier,
                "usable": entry.model_id != PLACEHOLDER_MODEL_ID,
                "key_configured": has_usable_key(entry),
                "fallback": entry.fallback,
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
    if body.tier not in VALID_TIERS and body.tier not in _registry:
        raise HTTPException(
            status_code=400,
            detail=f"invalid tier/model: {body.tier!r}, must be one of {VALID_TIERS} or a real model name",
        )
    get_db().set_role_tier(body.user, role, body.tier)
    return {"user": body.user, "role": role, "tier": body.tier}


class CreateEvalRunRequest(BaseModel):
    user: str
    # A real models.yaml entry name (same registry admin-ui's Model Tiering page
    # already lists), not a raw provider/model_id pair — the judge is just
    # another registry model, picked the same way a role's model is picked.
    model: str
    limit: int = 50


@app.post("/v1/admin/evals", dependencies=[Depends(_check_auth)])
def create_eval_run(body: CreateEvalRunRequest) -> dict:
    """Kick off a background LLM-as-judge eval run (gateway/eval_runner.py) over
    the most recent real gateway conversations, graded by the chosen registry
    model. Runs in Starlette's threadpool via BackgroundTask (eval_runner.run_eval
    is sync — judge calls are blocking network I/O), so the request returns
    immediately with a run id admin-ui polls via GET .../evals/{id}; the run
    itself keeps going even if that tab is closed, same as memory extraction's
    BackgroundTask pattern above.
    """
    if body.model not in _registry:
        raise HTTPException(status_code=400, detail=f"unknown model: {body.model!r}")
    entry = _registry[body.model]
    if entry.model_id == PLACEHOLDER_MODEL_ID:
        raise HTTPException(status_code=400, detail=f"{body.model!r} has no usable model_id yet")

    # phoenix.evals' LLM wrapper only knows "anthropic"/"openai" as provider
    # names — openai_responses is a Strands-only distinction (tool-calling API
    # shape), irrelevant for a judge that only classifies plain text.
    judge_provider = "anthropic" if entry.provider == "anthropic" else "openai"

    db = get_db()
    run_id = db.create_eval_run(body.user, judge_provider, entry.model_id)
    background = BackgroundTask(eval_runner.run_eval, db, run_id, judge_provider, entry.model_id, body.limit)
    return JSONResponse({"id": run_id}, background=background)


@app.get("/v1/admin/evals", dependencies=[Depends(_check_auth)])
def list_eval_runs(limit: int = 20) -> dict:
    return {"data": [_serialize_eval_run(row) for row in get_db().list_eval_runs(limit)]}


@app.get("/v1/admin/evals/{run_id}", dependencies=[Depends(_check_auth)])
def get_eval_run(run_id: int) -> dict:
    row = get_db().get_eval_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    return _serialize_eval_run(row)


@app.post("/v1/admin/evals/{run_id}/cancel", dependencies=[Depends(_check_auth)])
def cancel_eval_run(run_id: int) -> dict:
    """Only sets a flag — gateway/eval_runner.py's run_eval checks it once per
    conversation and stops there, since a judge LLM call already sent can't be
    interrupted mid-flight. Status stays 'running' until it actually notices."""
    row = get_db().get_eval_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="eval run not found")
    applied = get_db().request_eval_run_cancel(run_id)
    if not applied:
        raise HTTPException(status_code=400, detail=f"run is already {row['status']}, nothing to cancel")
    return {"id": run_id, "cancel_requested": True}


def _serialize_eval_run(row: dict) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "judge_provider": row["judge_provider"],
        "judge_model": row["judge_model"],
        "status": row["status"],
        "cancel_requested": row["cancel_requested"],
        "total": row["total"],
        "completed": row["completed"],
        "summary": row["summary"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _short_circuit_title_response(model_name: str) -> JSONResponse:
    return JSONResponse(
        compat.completion_response(compat.completion_id(), model_name, "IDX Analyst chat")
    )
