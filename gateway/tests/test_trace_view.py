from gateway.trace_view import summarize


def span(id_, parent, kind, name, start, end, attrs=None, events=None, status="OK"):
    return {
        "name": name, "span_kind": kind, "context": {"span_id": id_, "trace_id": "t"}, "parent_id": parent,
        "start_time": f"2026-10-03T00:00:{start:06.3f}+00:00", "end_time": f"2026-10-03T00:00:{end:06.3f}+00:00",
        "status_code": status, "attributes": attrs or {}, "events": events or [],
    }


def test_summarize_nests_steps_and_totals_credits():
    spans = [
        span("r", None, "AGENT", "chat_completion", 0, 10),
        span("c", "r", "CHAIN", "invoke_agent", 0.1, 9),
        span("m", "c", "AGENT", "execute_tool market_intelligence", 1, 8, {"tool.name": "market_intelligence"}),
        span("i", "m", "AGENT", "invoke_agent market_intelligence", 1.1, 7.9),
        span("t", "i", "TOOL", "get_company_report", 2, 4, {"tool.name": "get_company_report"},
             [{"name": "sectors_call", "attributes": {"credits": 3, "call": "x"}}]),
        span("l", "c", "LLM", "messages.stream", 4, 5, {"llm.model_name": "claude", "llm.token_count.total": 120}),
    ]
    out = summarize(spans)
    assert [(s["name"], s["depth"]) for s in out["steps"]] == [
        ("market_intelligence", 0), ("get_company_report", 1), ("claude", 0)
    ]
    assert out["credits"] == 3 and out["tokens"] == 120 and out["duration_ms"] == 10000


def test_summarize_waits_for_root_span():
    assert summarize([span("t", "r", "TOOL", "x", 1, 2)]) is None
