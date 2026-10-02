from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from data import credit_gate


def test_record_spend_adds_event_to_current_span():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("t").start_as_current_span("tool"):
        credit_gate.record_spend("company_report BBCA", 2)
    (event,) = exporter.get_finished_spans()[0].events
    assert event.name == "sectors_call" and event.attributes["credits"] == 2
