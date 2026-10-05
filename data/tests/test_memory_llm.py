"""With no model key at all, the memory helpers must not call any provider (a blank-key
call shows up as an error span in Phoenix) and must fall back to their safe defaults."""

from data import memory_llm


def test_no_key_means_no_call_and_safe_defaults(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert memory_llm._complete("hi", 10) is None
    assert memory_llm.extract_from_transcript("User: hi") == {}
    assert memory_llm.decide_consolidation("x", [{"id": 1, "content": "y"}]) == {"action": "ADD", "target_id": None}


def test_openai_key_alone_picks_openai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # blank counts as unset
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    called = {}

    class _Chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                called.update(kwargs)
                msg = type("M", (), {"content": '{"facts": ["a"]}'})
                return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    monkeypatch.setattr("openai.OpenAI", lambda: type("O", (), {"chat": _Chat})())
    assert memory_llm.extract_from_transcript("User: hi") == {"facts": ["a"]}
    assert called["model"] == "gpt-5.6-luna"
