"""FastAPI gateway exposing an OpenAI-compatible API in front of a Strands agent.

See idx_agent_infrastructure_diagrams_md.md sections 2-4.
"""

import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from gateway import compat
from gateway.agent import build_agent
from gateway.guardrails import (
    RateLimitExceededError,
    attach_disclaimer,
    enforce_rate_limit,
)
from gateway.registry import load_registry
from gateway.telemetry import setup_telemetry, traced_conversation
from data.deps import get_cache

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


@app.get("/v1/models", dependencies=[Depends(_check_auth)])
async def list_models() -> dict:
    """Return the model names LibreChat can select from."""
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "owned_by": "idx-agent"}
            for name in _registry
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
    strands_messages = compat.to_strands_messages(chat_messages)
    question = compat.latest_user_text(chat_messages)

    agent = _build_agent_with_fallback(model_entry)
    if system_prompt:
        agent.system_prompt = f"{agent.system_prompt}\n\n{system_prompt}"

    if stream:
        return StreamingResponse(
            _stream_response(agent, strands_messages, model_name, question, user_id),
            media_type="text/event-stream",
        )

    with traced_conversation(question, model_name, user_id) as span:
        text = await _run_to_completion(agent, strands_messages)
        answer = attach_disclaimer(text)
        span.set_output(answer)
    return JSONResponse(compat.completion_response(compat.completion_id(), model_name, answer))


def _build_agent_with_fallback(model_entry):
    try:
        return build_agent(model_entry)
    except Exception:
        if not model_entry.fallback:
            raise
        return build_agent(_registry[model_entry.fallback])


async def _run_to_completion(agent, messages: list[dict]) -> str:
    chunks: list[str] = []
    async for event in agent.stream_async(prompt=messages):
        if "data" in event:
            chunks.append(event["data"])
    return "".join(chunks)


async def _stream_response(agent, messages: list[dict], model_name: str, question: str, user_id: str):
    completion_id_ = compat.completion_id()
    chunks: list[str] = []
    with traced_conversation(question, model_name, user_id) as span:
        async for event in agent.stream_async(prompt=messages):
            if "data" in event and event["data"]:
                chunks.append(event["data"])
                yield compat.sse_chunk(completion_id_, model_name, {"content": event["data"]})
        span.set_output("".join(chunks))
    yield compat.sse_chunk(completion_id_, model_name, {}, finish_reason="stop")
    yield compat.sse_done()


def _short_circuit_title_response(model_name: str) -> JSONResponse:
    return JSONResponse(
        compat.completion_response(compat.completion_id(), model_name, "IDX Analyst chat")
    )
