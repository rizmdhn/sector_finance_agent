"""The two small LLM calls AgentCore-style memory needs: deciding whether a new
fact should be ADDed, should UPDATE (supersede) an existing one, or is a SKIP
(already known) — and, separately, extracting facts/preferences/a summary out
of a finished conversation. See data/memory_store.py for how the results get
written, and gateway/memory_extraction.py for what schedules the extraction
call.

Both call Anthropic directly with a fixed cheap model (same one models.yaml
picks for its "cheap" tier), not through gateway/registry.py's per-role
tiering — these aren't a role in the agent graph, just fixed-purpose utility
calls, and data/ has no business importing gateway/ (see README's "data/ is
imported by both gateway/ and ingest/" — the dependency only goes one way).
`anthropic` is already a project dependency (gateway/registry.py's
AnthropicModel uses the same SDK under the hood via Strands).

Both fail closed rather than raising: a broken consolidation call returns ADD
(worst case, a harmless duplicate) rather than losing a fact; a broken
extraction call returns nothing rather than corrupting the conversation it was
extracting from. Real API cost either way, gated entirely behind
auto_extraction (data/db.py::get_memory_settings) by every caller.
"""

import json
import os

_MODEL = "claude-haiku-4-5-20251001"


def _client():
    from anthropic import Anthropic

    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _parse_json(text: str) -> dict:
    """Haiku reliably wraps JSON in a ```json ... ``` fence despite being asked
    for "ONLY a JSON object" — confirmed live (a first attempt at this parsed
    with plain json.loads(), got silently swallowed by the caller's except
    Exception, and looked like the model had extracted nothing at all). Strip a
    fence if present before parsing."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text.strip())


def decide_consolidation(new_content: str, candidates: list[dict]) -> dict:
    """`candidates` are the top few existing memories closest to `new_content` by
    embedding distance (data/db.py::vector_search_user_memory). Returns
    {"action": "ADD"|"UPDATE"|"SKIP", "target_id": <id>|None}."""
    candidate_list = "\n".join(f'- id={c["id"]}: "{c["content"]}"' for c in candidates)
    prompt = f"""New fact: "{new_content}"

Existing similar facts:
{candidate_list}

Decide: does the new fact add genuinely new information (ADD), update or replace \
one of the existing facts because it's now outdated (UPDATE), or say nothing the \
existing facts don't already say (SKIP)? Respond with ONLY a JSON object: \
{{"action": "ADD" or "UPDATE" or "SKIP", "target_id": <id of the fact it replaces, or null>}}"""
    try:
        response = _client().messages.create(
            model=_MODEL, max_tokens=100, messages=[{"role": "user", "content": prompt}]
        )
        return _parse_json(response.content[0].text)
    except Exception:
        return {"action": "ADD", "target_id": None}


def extract_from_transcript(transcript: str) -> dict:
    """Returns {"facts": [...], "preferences": [...], "summary": str|None},
    any/all empty when nothing in this conversation was worth extracting."""
    prompt = f"""Conversation:
{transcript}

Extract, if present:
- "facts": durable facts about the user worth remembering long-term (portfolio \
positions, mandate limits, recorded investment theses) — not facts about a \
company or the market in general, those don't belong here.
- "preferences": stated preferences about how the user wants answers (format, \
style, what to focus on).
- "summary": a 1-2 sentence summary of what this conversation covered, or null if \
there's nothing worth summarizing (e.g. a single simple lookup with no real \
back-and-forth).

Respond with ONLY a JSON object: {{"facts": [...], "preferences": [...], \
"summary": "..." or null}}. Empty arrays if nothing qualifies."""
    try:
        response = _client().messages.create(
            model=_MODEL, max_tokens=500, messages=[{"role": "user", "content": prompt}]
        )
        return _parse_json(response.content[0].text)
    except Exception:
        return {}
