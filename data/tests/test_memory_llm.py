"""Pure unit test for data/memory_llm.py's JSON parsing — no API calls, no
Postgres. Exists because a real bug slipped through here once already: Haiku
reliably wraps its JSON in a ```json fence despite being told "ONLY a JSON
object", and json.loads() on the raw text failed silently (swallowed by the
caller's `except Exception`), making extraction look like it found nothing at
all rather than erroring loudly. See PROGRESS.md for the live conversation
that surfaced this."""

from data.memory_llm import _parse_json


def test_parses_fenced_json():
    text = '```json\n{"facts": ["a"], "preferences": [], "summary": null}\n```'
    assert _parse_json(text) == {"facts": ["a"], "preferences": [], "summary": None}


def test_parses_plain_json():
    text = '{"action": "ADD", "target_id": null}'
    assert _parse_json(text) == {"action": "ADD", "target_id": None}


def test_parses_fenced_json_without_language_tag():
    text = '```\n{"action": "SKIP", "target_id": 5}\n```'
    assert _parse_json(text) == {"action": "SKIP", "target_id": 5}
