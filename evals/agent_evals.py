"""Arize Phoenix's own purpose-built AGENT evaluators — ToolSelectionEvaluator,
ToolInvocationEvaluator, ToolResponseHandlingEvaluator (`phoenix.evals.metrics`) —
run against this project's real tool-calling traces, instead of evals/
phoenix_evals.py's hand-rolled text-only classifiers.

Different question from phoenix_evals.py: that script asks "does the final answer
TEXT follow our compliance rules" (dated figures, no recommendation, discloses
gaps). This script asks "did the AGENT use its TOOLS correctly": did the Chief pick
the right specialist for the question (ToolSelectionEvaluator), did each real data
tool get called with valid, non-hallucinated arguments (ToolInvocationEvaluator),
and did the final answer correctly reflect what the tool actually returned rather
than mishandling or hallucinating on top of it (ToolResponseHandlingEvaluator). Both
scripts' results land as span annotations in the same Phoenix project, visible in
its own UI — this script doesn't build any separate presentation surface.

Two levels of evaluation, both read straight from the real OpenTelemetry trace tree
(no new agent-side LLM/Sectors calls — only new judge calls):
  - CHIEF-level tool SELECTION: for each conversation, which specialist(s) did the
    Chief call, given the real list of specialists it had available (read live from
    a real Agent's tool_registry, not hardcoded, so it can't drift from
    gateway/roles/orchestrator.py).
  - Leaf-level tool INVOCATION: for every real data-fetching tool call
    (analyze_fundamentals, get_company_report, get_price_history, ..., excluding the
    specialist-delegation "meta-tools" themselves, which are evaluated as
    selections, not invocations) found anywhere in the trace tree, however deep —
    each judged with the tool's real JSON schema and the immediate specialist's own
    task text plus its prior tool calls in that turn as context, not just the
    top-level question (a first pass without that context misread every legitimate
    peer-bank comparison call, e.g. analyze_fundamentals(BBRI) alongside
    analyze_fundamentals(BBCA), as a "wrong symbol" error — see PROGRESS.md item 25).

RESPONSE HANDLING is deliberately NOT run by default (see --metrics). Phoenix's
ToolResponseHandlingEvaluator assumes one tool call maps to one output — its own
docstring examples are exactly that shape. This project's specialists routinely
call 2-4 tools and synthesize ONE answer from all of them, so comparing any single
tool's raw result against that synthesized text reads as "hallucinated extra data"
almost every time, even when the specialist handled every tool correctly — the
"extra" data legitimately came from its OTHER tool calls in the same turn, not
fabrication. A first live run confirmed this: nearly every leaf call scored
"incorrect" on response handling, including ones whose invocation was clearly
correct and whose result the specialist visibly used right. This evaluator is a
good fit for a single-tool-call agent; using it here without deeper engineering
(isolating true per-call intermediate synthesis, which this project's traces don't
cleanly capture today) would produce misleading annotations in Phoenix. Pass
--metrics tool_response_handling explicitly if you want to see this for yourself.

Usage:
    python evals/agent_evals.py --limit 5
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Only set when run standalone — see evals/phoenix_evals.py's matching comment:
# setting this unconditionally at import time poisons gateway.telemetry.PROJECT_NAME
# for any process that imports this module as a library instead of running it as a
# script.
if __name__ == "__main__":
    os.environ.setdefault("PHOENIX_PROJECT_NAME", "idx-agent-evals")

from gateway.telemetry import setup_telemetry
from phoenix.client import Client
from phoenix.evals import LLM
from phoenix.evals.metrics import ToolInvocationEvaluator, ToolResponseHandlingEvaluator, ToolSelectionEvaluator

DEFAULT_PHOENIX_URL = "http://localhost:6006"
DEFAULT_PROJECT = "idx-agent-gateway"

# Specialist/meta tools are evaluated as SELECTIONS (did the Chief pick the right
# one), never as leaf INVOCATIONS (their "arguments" are just a free-text prompt to
# a sub-agent, not the structured params ToolInvocationEvaluator is built to check).
SPECIALIST_TOOL_NAMES = {
    "investment_research_lead",
    "portfolio_risk_lead",
    "market_and_event_intelligence_lead",
    "independent_risk_and_evidence_officer",
}


def _available_tools_text() -> str:
    """Real tool list read from a live Chief build (gateway/roles/orchestrator.py),
    not hardcoded — so this can't silently drift from what the Chief actually has.
    """
    from data.deps import get_cache, get_db
    from gateway.registry import load_registry
    from gateway.roles.orchestrator import build_agent

    registry = load_registry()
    entry = registry["idx-analyst-claude"]
    agent = build_agent(entry, db=get_db(), cache=get_cache(), user_id="agent-evals-introspection", session_id="agent-evals-introspection")
    lines = []
    for tool in agent.tool_registry.registry.values():
        desc = (tool.tool_spec.get("description") or "").split("\n")[0]
        lines.append(f"{tool.tool_name}: {desc}")
    return "\n".join(lines)


def _event_text(events, name: str) -> str | None:
    for e in events or []:
        if e.get("name") == name:
            content = e.get("attributes", {}).get("content") or e.get("attributes", {}).get("message")
            return content
    return None


def _io(row, events_name: str, attr: str) -> str | None:
    """A span's tool arguments (`input.value`) / result (`output.value`). The gateway's span
    converter (gateway/telemetry.py) rewrites Strands' spans to OpenInference and drops the
    `gen_ai.tool.message` / `gen_ai.choice` events these used to live in, so read the
    attributes first and fall back to the events for traces recorded before that."""
    value = row.get(attr)
    return value if isinstance(value, str) and value else _event_text(row.get("events"), events_name)


def _json_schema(row) -> str | None:
    gen_ai = row.get("attributes.gen_ai")
    if isinstance(gen_ai, dict):
        return gen_ai.get("tool.json_schema")
    return None


def _nearest_specialist_ancestor(tree, span_id: str):
    """Walk parent_id up from `span_id` until hitting an `execute_tool <specialist>`
    span (a delegation boundary) or running out of tree. Returns that span's
    (span_id, row) or (None, None) if this call was made directly by the Chief
    (e.g. search_memory/add_memory) with no specialist in between.
    """
    current = tree.loc[span_id, "parent_id"] if span_id in tree.index else None
    while current is not None and current in tree.index:
        row = tree.loc[current]
        name = row["name"]
        if isinstance(name, str) and name.startswith("execute_tool "):
            tool_name = name.removeprefix("execute_tool ")
            if tool_name in SPECIALIST_TOOL_NAMES:
                return current, row
        current = row.get("parent_id")
    return None, None


def _collect(df, chat_span_id: str):
    """Walk one conversation's full trace tree (all spans sharing its trace_id) and
    return (selections, leaf_calls).

    Each leaf_call's `input`/`output` are built from its OWN nearest specialist
    ancestor, not the whole conversation, and carry the sibling tool calls made
    earlier in that same specialist's turn as explicit trajectory context — a
    first pass here fed every leaf call the top-level user question alone and the
    conversation's final answer, which made the Independent Risk and Evidence
    Officer's legitimate peer-bank comparisons (analyze_fundamentals for BBRI/
    BMRI/BBNI alongside BBCA — see PROGRESS.md item 22/25) read as "wrong symbol"
    errors, and made every tool's response look "hallucinated against" because the
    final answer synthesizes many OTHER tool calls' data too. Fixed by scoping
    both to the immediate specialist call, and schema to the tool's own real
    JSON schema instead of a bare description.
    """
    trace_id = df.loc[chat_span_id, "context.trace_id"]
    tree = df[df["context.trace_id"] == trace_id]
    top_question = df.loc[chat_span_id, "attributes.input.value"]

    chief_span = tree[tree["name"].str.startswith("invoke_agent chief", na=False)]
    selections = []
    if not chief_span.empty:
        chief_id = chief_span.index[0]
        # Specialists are only ever called by the Chief, but their spans hang off its
        # event-loop cycle, not off `invoke_agent chief` itself — match on tool name.
        chosen_names = [
            n.removeprefix("execute_tool ")
            for n in tree.sort_values("start_time")["name"]
            if isinstance(n, str) and n.removeprefix("execute_tool ") in SPECIALIST_TOOL_NAMES and n.startswith("execute_tool ")
        ]
        if chosen_names:
            selections.append((chief_id, chosen_names))

    # All leaf tool spans in chronological order, so "prior sibling calls" can be
    # computed by a simple earlier-index lookup once grouped by specialist ancestor.
    leaf_rows = [
        (span_id, row)
        for span_id, row in tree.sort_values("start_time").iterrows()
        if isinstance(row["name"], str)
        and row["name"].startswith("execute_tool ")
        and row["name"].removeprefix("execute_tool ") not in SPECIALIST_TOOL_NAMES
    ]

    prior_by_specialist: dict[str | None, list[str]] = defaultdict(list)
    leaf_calls = []
    for span_id, row in leaf_rows:
        tool_name = row["name"].removeprefix("execute_tool ")
        specialist_id, specialist_row = _nearest_specialist_ancestor(tree, span_id)

        if specialist_row is not None:
            task = _io(specialist_row, "gen_ai.tool.message", "attributes.input.value") or ""
            try:  # the delegation's argument is {"input": "<task text>"} — show just the text
                task = json.loads(task)["input"]
            except (ValueError, KeyError, TypeError):
                pass
            specialist_output = _io(specialist_row, "gen_ai.choice", "attributes.output.value") or ""
            context_label = specialist_row["name"].removeprefix("execute_tool ")
        else:
            task = ""
            specialist_output = df.loc[chat_span_id, "attributes.output.value"] or ""
            context_label = "chief_portfolio_intelligence_orchestrator"

        prior = prior_by_specialist[specialist_id]
        # Include each prior call's RESULT, not just its arguments — a value one
        # call passes to a later one (e.g. analyze_portfolio's position_values fed
        # into analyze_liquidity's position_value) is only verifiable against the
        # actual number that came back, not the fact that some call happened. An
        # earlier version of this context only listed prior calls' arguments,
        # which made a genuinely correct dependent-value invocation (real fix
        # verified in PROGRESS.md item 26) read as "unverifiable/hallucinated"
        # since the judge never saw where the number actually came from.
        trajectory = f" Already called in this turn: {'; '.join(prior)}." if prior else ""
        input_text = (
            f"User's original question: {top_question}\n"
            f"This call was made by: {context_label}, working on: {task}.{trajectory}"
        )

        args = _io(row, "gen_ai.tool.message", "attributes.input.value")
        result = _io(row, "gen_ai.choice", "attributes.output.value")
        leaf_calls.append(
            {
                "span_id": span_id,
                "tool_name": tool_name,
                "description": row.get("attributes.tool.description") or "",
                "schema": _json_schema(row),
                "args": args,
                "result": result,
                "input_text": input_text,
                "output_text": specialist_output,
            }
        )
        prior_by_specialist[specialist_id].append(f"{tool_name}({args}) -> {result}")

    return selections, leaf_calls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phoenix-url", default=DEFAULT_PHOENIX_URL)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--provider", default="anthropic")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--limit", type=int, default=5, help="Max recent chat_completion conversations to pull")
    parser.add_argument("--no-log-annotations", dest="log_annotations", action="store_false")
    parser.add_argument(
        "--metrics",
        nargs="*",
        default=["tool_selection", "tool_invocation"],
        choices=["tool_selection", "tool_invocation", "tool_response_handling"],
        help="Which evaluators to run. tool_response_handling is opt-in, not default — see module "
        "docstring for why it's unreliable for this project's multi-tool-call-then-synthesize agents.",
    )
    args = parser.parse_args()

    os.environ.setdefault("PHOENIX_COLLECTOR_ENDPOINT", args.phoenix_url)
    setup_telemetry()

    client = Client(base_url=args.phoenix_url)
    df = client.spans.get_spans_dataframe(project_name=args.project, limit=2000)
    chats = df[df["name"] == "chat_completion"].sort_values("start_time", ascending=False).head(args.limit)
    if chats.empty:
        print(f"No 'chat_completion' conversations found in project '{args.project}'. Run scripts/generate_eval_conversations.py first.")
        return

    judge = LLM(provider=args.provider, model=args.model)
    tool_selection_eval = ToolSelectionEvaluator(llm=judge) if "tool_selection" in args.metrics else None
    tool_invocation_eval = ToolInvocationEvaluator(llm=judge) if "tool_invocation" in args.metrics else None
    tool_response_eval = ToolResponseHandlingEvaluator(llm=judge) if "tool_response_handling" in args.metrics else None
    available_tools = _available_tools_text()

    annotations = []
    for chat_span_id, chat_row in chats.iterrows():
        question = chat_row.get("attributes.input.value")
        answer = chat_row.get("attributes.output.value")
        selections, leaf_calls = _collect(df, chat_span_id)
        print(f"\n=== {question!r} ===")

        for chief_id, chosen_names in (selections if tool_selection_eval else []):
            eval_input = {
                "input": f"User: {question}",
                "available_tools": available_tools,
                "tool_selection": ", ".join(f"{n}(...)" for n in chosen_names),
            }
            scores = tool_selection_eval.evaluate(eval_input)
            for score in scores:
                print(f"  [tool_selection] chose {chosen_names} -> {score.label} :: {score.explanation[:150]}")
                if args.log_annotations:
                    annotations.append(
                        {
                            "name": "tool_selection",
                            "annotator_kind": "LLM",
                            "span_id": chief_id,
                            "result": {"label": score.label, "score": score.score, "explanation": score.explanation},
                        }
                    )

        for call in leaf_calls:
            if not call["args"] or not call["result"]:
                continue
            tool_call_str = f"{call['tool_name']}({call['args']})"
            schema_text = call["schema"] or f"{call['tool_name']}: {call['description']}"

            inv_input = {
                "input": call["input_text"],
                "available_tools": schema_text,
                "tool_selection": tool_call_str,
            }
            for score in (tool_invocation_eval.evaluate(inv_input) if tool_invocation_eval else []):
                print(f"  [tool_invocation] {call['tool_name']} -> {score.label} :: {score.explanation[:150]}")
                if args.log_annotations:
                    annotations.append(
                        {
                            "name": "tool_invocation",
                            "annotator_kind": "LLM",
                            "span_id": call["span_id"],
                            "result": {"label": score.label, "score": score.score, "explanation": score.explanation},
                        }
                    )

            resp_input = {
                "input": call["input_text"],
                "tool_call": tool_call_str,
                "tool_result": call["result"],
                "output": call["output_text"],
            }
            for score in (tool_response_eval.evaluate(resp_input) if tool_response_eval else []):
                print(f"  [tool_response_handling] {call['tool_name']} -> {score.label} :: {score.explanation[:150]}")
                if args.log_annotations:
                    annotations.append(
                        {
                            "name": "tool_response_handling",
                            "annotator_kind": "LLM",
                            "span_id": call["span_id"],
                            "result": {"label": score.label, "score": score.score, "explanation": score.explanation},
                        }
                    )

    if args.log_annotations and annotations:
        client.spans.log_span_annotations(span_annotations=annotations)
        print(f"\nlogged {len(annotations)} annotations back to Phoenix (visible in its trace UI, per-span)")

    from opentelemetry import trace as trace_api

    trace_api.get_tracer_provider().force_flush(timeout_millis=5000)


if __name__ == "__main__":
    main()
