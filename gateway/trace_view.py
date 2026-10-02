"""Flattens one chat turn's trace (read back from Phoenix) into the step list the admin
UI shows: which agents ran, which tools they called, tokens, and Sectors credits.

Phoenix is the source of truth — nothing is tracked separately in the gateway. Credits
come from the `sectors_call` span events data/credit_gate.py records on real calls.
"""

import os
from datetime import datetime

import httpx

from gateway.telemetry import DEFAULT_PHOENIX_ENDPOINT, PROJECT_NAME

SHOWN_KINDS = {"AGENT", "TOOL", "LLM"}
ROOT_SPAN = "chat_completion"


def _shown(span: dict) -> bool:
    # `invoke_agent X` is the inner run of a delegation whose outer `execute_tool X` span
    # already carries the same name and timing — showing both would list every agent twice.
    return span["span_kind"] in SHOWN_KINDS and span["name"] != ROOT_SPAN and not span["name"].startswith("invoke_agent")


def _ms(start: str, end: str) -> int:
    return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000)


def summarize(spans: list[dict]) -> dict | None:
    """None until the root `chat_completion` span has arrived (it ends last, and the
    exporter ships spans in batches), so a partial trace is never shown as complete."""
    root = next((s for s in spans if s["name"] == ROOT_SPAN), None)
    if root is None:
        return None
    by_id = {s["context"]["span_id"]: s for s in spans}

    def depth(span: dict) -> int:
        # Steps nest under their nearest *shown* ancestor; Strands' own plumbing spans
        # (event loop cycles, `chat`) are skipped, not counted.
        d, parent = 0, by_id.get(span.get("parent_id"))
        while parent is not None:
            d += _shown(parent)
            parent = by_id.get(parent.get("parent_id"))
        return d

    steps, credits, tokens = [], 0, 0
    for span in sorted(spans, key=lambda s: s["start_time"]):
        attrs = span.get("attributes", {})
        spent = sum(
            int(e.get("attributes", {}).get("credits", 0)) for e in span.get("events") or [] if e.get("name") == "sectors_call"
        )
        credits += spent
        if not _shown(span):
            continue
        span_tokens = int(attrs.get("llm.token_count.total") or 0) if span["span_kind"] == "LLM" else 0
        tokens += span_tokens
        steps.append({
            "kind": span["span_kind"],
            "name": attrs.get("tool.name") or attrs.get("llm.model_name") or span["name"],
            "depth": depth(span),
            "offset_ms": _ms(root["start_time"], span["start_time"]),
            "duration_ms": _ms(span["start_time"], span["end_time"]),
            "tokens": span_tokens,
            "credits": spent,
            "error": span["status_code"] == "ERROR",
        })
    return {
        "duration_ms": _ms(root["start_time"], root["end_time"]),
        "credits": credits,
        "tokens": tokens,
        "llm_calls": sum(1 for s in steps if s["kind"] == "LLM"),
        "steps": steps,
    }


def fetch_trace(trace_id: str) -> dict | None:
    base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_PHOENIX_ENDPOINT).rstrip("/")
    response = httpx.get(f"{base}/v1/projects/{PROJECT_NAME}/spans", params={"trace_id": trace_id, "limit": 1000}, timeout=10)
    response.raise_for_status()
    summary = summarize(response.json()["data"])
    if summary is not None:
        summary["phoenix_url"] = _phoenix_link(base, trace_id)
    return summary


def _phoenix_link(base: str, trace_id: str) -> str | None:
    """Deep link for a browser: PHOENIX_PUBLIC_URL (the address the *user* reaches Phoenix
    at — the Compose-internal `phoenix:6006` won't resolve there)."""
    public = os.environ.get("PHOENIX_PUBLIC_URL", "http://localhost:6006").rstrip("/")
    try:
        project = httpx.get(f"{base}/v1/projects/{PROJECT_NAME}", timeout=5).json()["data"]["id"]
    except Exception:
        return None
    return f"{public}/projects/{project}/traces/{trace_id}"
