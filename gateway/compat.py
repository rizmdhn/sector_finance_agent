"""Conversions between the OpenAI chat-completions wire format LibreChat speaks
and the Strands Agent's message/event shapes.

See idx_agent_infrastructure_diagrams_md.md section 3-4.
"""

import json
import time
import uuid

from strands.types.content import Message

TITLE_REQUEST_MARKERS = ("generate a title", "concise title", "title for this conversation")


def split_system_prompt(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Pull out OpenAI "system" messages; Strands takes the system prompt separately."""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, rest


def to_strands_messages(messages: list[dict]) -> list[Message]:
    """Convert OpenAI {"role", "content"} messages to Strands Message objects.
    Only "user" and "assistant" roles are valid on the Strands side.
    """
    return [
        {"role": m["role"], "content": [{"text": m["content"]}]}
        for m in messages
        if m["role"] in ("user", "assistant")
    ]


def latest_user_text(messages: list[dict]) -> str:
    """Extract the latest user message's text from OpenAI-shape messages (plain
    string `content`, before `to_strands_messages` wraps it into content blocks),
    for tracing (gateway/telemetry.py) — the OpenInference INPUT_VALUE attribute is
    a single string, not a message list.
    """
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "") or ""
    return ""


def is_title_request(messages: list[dict]) -> bool:
    """Detect LibreChat's conversation-title-generation calls so they can be
    short-circuited instead of running the full agent + tools.

    TODO: confirm the exact prompt LibreChat sends (idx_agent_infrastructure_diagrams_md.md
    section 3, "Title generation").
    """
    if not messages:
        return False
    text = " ".join(m.get("content", "") for m in messages).lower()
    return any(marker in text for marker in TITLE_REQUEST_MARKERS)


def completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def now_epoch() -> int:
    return int(time.time())


def sse_chunk(completion_id_: str, model: str, delta: dict, finish_reason: str | None = None) -> str:
    payload = {
        "id": completion_id_,
        "object": "chat.completion.chunk",
        "created": now_epoch(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def sse_done() -> str:
    return "data: [DONE]\n\n"


def completion_response(completion_id_: str, model: str, content: str) -> dict:
    return {
        "id": completion_id_,
        "object": "chat.completion",
        "created": now_epoch(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _dumps(payload: dict) -> str:
    import json

    return json.dumps(payload)
