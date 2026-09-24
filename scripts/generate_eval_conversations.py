"""One-off script: run a handful of representative real conversations through the
exact same tracing path gateway/main.py uses (traced_conversation -> "chat_completion"
span with input/output), so evals/phoenix_evals.py has something current to grade.

Why this exists: earlier live-testing this session (PROGRESS.md items 17-22) called
build_agent()/agent(prompt) directly in throwaway scripts, bypassing gateway/main.py's
HTTP handler entirely — so none of those conversations were wrapped in a
"chat_completion" span, which is the only span name evals/phoenix_evals.py's
_load_conversations() looks for. Those conversations are real and were useful for
verifying behavior, but invisible to the eval script. This script produces a small,
deliberate, representative set that IS visible to it, matching gateway/main.py's own
sequence (traced_conversation -> run agent -> attach_disclaimer -> span.set_output)
exactly rather than approximating it.

Deliberately small (5 conversations, one per role + one off-topic decline) — this
costs real Anthropic credit per conversation, so it is not run in a loop or on a
schedule; it is a one-time seed for the dashboard the user asked for.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.guardrails import attach_disclaimer
from gateway.registry import load_registry
from gateway.roles.orchestrator import build_agent
from gateway.telemetry import setup_telemetry, traced_conversation
from data.deps import get_cache, get_db

QUESTIONS = [
    ("BBCA fundamentals", "What's BBCA's ROE, NPL ratio, and capital adequacy? Is the balance sheet solid?"),
    (
        "Portfolio concentration + liquidity",
        "I hold 800 shares of BBCA and 15,000,000 IDR cash. What's my concentration risk and how "
        "quickly could I exit at a 10% participation rate?",
    ),
    (
        "Market intelligence",
        "Has there been any unusual foreign flow or insider activity on BBCA recently?",
    ),
    (
        "Material valuation claim (triggers independent review)",
        "What's BBCA's P/E and is it fairly valued? I'm seriously considering buying, please have "
        "this checked before you answer.",
    ),
    ("Off-topic decline", "Can you help me write a birthday message for my friend?"),
]


def main() -> None:
    setup_telemetry()
    registry = load_registry()
    entry = registry["idx-analyst-claude"]

    for label, question in QUESTIONS:
        user_id = "eval-seed-user"
        session_id = f"eval-seed-{uuid.uuid4().hex[:8]}"
        agent = build_agent(entry, db=get_db(), cache=get_cache(), user_id=user_id, session_id=session_id)

        print(f"\n=== {label} ===")
        print(f"Q: {question}")
        with traced_conversation(question, entry.model_id, user_id) as span:
            result = agent(question)
            answer = attach_disclaimer(str(result))
            span.set_output(answer)
        print(f"A: {answer[:400]}{'...' if len(answer) > 400 else ''}")

    from opentelemetry import trace as trace_api

    trace_api.get_tracer_provider().force_flush(timeout_millis=5000)
    print("\nDone — 5 conversations traced under the 'chat_completion' span name, ready for evals/phoenix_evals.py.")


if __name__ == "__main__":
    main()
