"""OpenTelemetry tracing for the gateway, exported to Arize Phoenix
(`docker-compose.yml`'s `phoenix` service; see idx_agent_infrastructure_diagrams_md.md
section 8).

Two layers of spans end up in Phoenix once a real ANTHROPIC_API_KEY/OPENAI_API_KEY is
added and a conversation runs — no further wiring needed for either:

1. Strands' own spans (LLM calls, tool calls, the agent-as-tool delegation from the
   Chief to Investment Research Lead) — created automatically by every `Agent`
   regardless of configuration (`strands/agent/agent.py` calls `get_tracer()`
   unconditionally in `__init__`). This module's only job for these is configuring
   the *global* OpenTelemetry tracer provider with an OTLP exporter pointed at
   Phoenix before any agent runs — `setup_telemetry()`, called once at gateway
   startup in `gateway/main.py`.
2. One wrapping span per gateway request (`traced_conversation`, used in
   `gateway/main.py`'s `chat_completions` handler), tagged with the OpenInference
   conventions (`input.value` / `output.value` / `openinference.span.kind=AGENT`)
   that Phoenix's UI and `evals/phoenix_evals.py` are built around. Strands' own
   spans use OTel's GenAI semantic conventions instead (`gen_ai.*`) — more detailed
   for debugging, but not the flat input/output shape the eval script needs, and not
   guaranteed to be the trace's root span (a title-generation short-circuit in
   `gateway/main.py` never touches an Agent at all, for instance). This wrapping
   span is what `evals/phoenix_evals.py` actually reads.

Verified live against a real (freshly started, empty) Phoenix container this
session, with no LLM involved: `setup_telemetry()` + `traced_conversation()` produces
a span Phoenix accepts and returns via `Client().spans.get_spans_dataframe()` with
exactly the `attributes.input.value` / `attributes.output.value` columns this module
assumes — see PROGRESS.md for the verification transcript.
"""

import contextlib
import functools
import logging
import os

from openinference.instrumentation.strands_agents import StrandsAgentsToOpenInferenceProcessor
from openinference.semconv.resource import ResourceAttributes
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from strands.telemetry import StrandsTelemetry
from strands.telemetry.config import get_otel_resource

logger = logging.getLogger(__name__)

DEFAULT_PHOENIX_ENDPOINT = "http://localhost:6006"
PROJECT_NAME = os.environ.get("PHOENIX_PROJECT_NAME", "idx-agent-gateway")

_tracer = trace.get_tracer(PROJECT_NAME)
_configured = False


def setup_telemetry() -> None:
    """Configure the global OTel tracer provider to export to Phoenix.

    Phoenix buckets traces into projects by the `openinference.project.name`
    resource attribute — NOT `service.name`, which is what Strands'
    `StrandsTelemetry()` sets by default (confirmed live: traces sent with only
    `service.name` set all landed under Phoenix's "default" project regardless of
    its value). A `Resource` combining both is built here and handed to
    `StrandsTelemetry(tracer_provider=...)` so Strands' own spans land in the same
    named project as `traced_conversation`'s spans below.

    Idempotent — safe to call more than once (e.g. under `uvicorn --reload`, which
    re-imports the app module). `PHOENIX_COLLECTOR_ENDPOINT` should point at the
    published host port (`http://localhost:6006`, the default, for a locally-run
    gateway) or the in-network service name (`http://phoenix:6006`, set in
    `docker-compose.yml`) when the gateway itself runs inside Compose.
    """
    global _configured
    if _configured:
        return

    base = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", DEFAULT_PHOENIX_ENDPOINT).rstrip("/")
    traces_endpoint = f"{base}/v1/traces"

    resource = get_otel_resource().merge(
        Resource.create({ResourceAttributes.PROJECT_NAME: PROJECT_NAME})
    )
    provider = TracerProvider(resource=resource)
    # Strands' own Tracer reads trace.get_tracer_provider() (the OTel global) at
    # construction time (strands/telemetry/tracer.py::Tracer.__init__) — passing a
    # tracer_provider to StrandsTelemetry() does NOT register it globally on its
    # own (only its no-arg branch does), so this call is set explicitly rather than
    # relying on that side effect.
    trace.set_tracer_provider(provider)

    # Strands emits OTel GenAI-convention spans (`gen_ai.*`), which Phoenix shows as
    # unclassified boxes — no agent/tool/LLM kind, no readable input/output, so it was
    # hard to tell when an agent or tool was actually called. This processor rewrites
    # them in place to OpenInference (AGENT / TOOL / LLM kinds, tool name + arguments +
    # result, token counts). It mutates spans on end, so it MUST be added before the
    # exporter below or the exporter ships the untranslated spans.
    provider.add_span_processor(_StrandsSpanConverter())

    telemetry = StrandsTelemetry(tracer_provider=provider)
    telemetry.setup_otlp_exporter(endpoint=traces_endpoint)
    if os.environ.get("OTEL_CONSOLE_EXPORT", "").lower() in ("1", "true"):
        telemetry.setup_console_exporter()

    # Strands' own spans already carry gen_ai.usage.* (token counts) explicitly, so
    # this instrumentor is redundant there — but any Anthropic call made OUTSIDE a
    # Strands Agent (e.g. evals/phoenix_evals.py's judge calls, which go through
    # phoenix.evals' own LLM wrapper) traces with no token-usage attributes at all
    # without it. Confirmed live: a judge call before this was added showed up in
    # Phoenix with the right input/output but no llm.token_count.* fields — this
    # patches the anthropic SDK client itself to record those, closing that gap.
    try:
        from openinference.instrumentation.anthropic import AnthropicInstrumentor

        AnthropicInstrumentor().instrument(tracer_provider=provider)
    except ImportError:
        logger.warning("openinference-instrumentation-anthropic not installed — "
                        "non-Strands Anthropic calls (e.g. eval judge calls) will "
                        "trace without token-usage attributes")
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        OpenAIInstrumentor().instrument(tracer_provider=provider)
    except ImportError:
        logger.warning("openinference-instrumentation-openai not installed — "
                        "non-Strands OpenAI calls will trace without token-usage "
                        "attributes")

    _configured = True
    logger.info("tracing configured: exporting to %s, project=%s", traces_endpoint, PROJECT_NAME)


@functools.cache
def _specialist_names() -> frozenset[str]:
    # Lazy: gateway.roles pulls in every tool/data dependency, which standalone
    # callers of this module (evals/phoenix_evals.py) shouldn't pay for at import.
    from gateway.roles.orchestrator import DEFAULT_ROLE_TIERS

    return frozenset(DEFAULT_ROLE_TIERS) - {"chief"}


class _StrandsSpanConverter(StrandsAgentsToOpenInferenceProcessor):
    """The stock converter marks Strands' own `chat` span as an LLM span, but the
    Anthropic/OpenAI instrumentors below already emit a real LLM span for the same
    call as its child (it carries `llm.provider`, which Phoenix needs to price the
    call). Confirmed live: both spans carried identical token counts, so Phoenix
    counted every model call's tokens and cost twice. Keep Strands' span as the
    structural parent (CHAIN) and let the SDK span be the one LLM span per call."""

    def on_end(self, span) -> None:
        super().on_end(span)
        attrs = span._attributes or {}
        # A specialist reaches the Chief via `.as_tool()`, so Strands emits its
        # delegation as a plain TOOL span. Label those AGENT so a delegation reads
        # differently from a real data tool (get_price_history etc.) in Phoenix.
        if (
            attrs.get(SpanAttributes.OPENINFERENCE_SPAN_KIND) == OpenInferenceSpanKindValues.TOOL.value
            and attrs.get(SpanAttributes.TOOL_NAME) in _specialist_names()
        ):
            span._attributes = attrs = {
                **attrs,
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
            }
        # Only Strands' own span (it keeps its original gen_ai.system="strands-agents") —
        # this processor sees every span, including the SDK instrumentors' real LLM span.
        if (
            attrs.get("gen_ai.system") == "strands-agents"
            and attrs.get(SpanAttributes.OPENINFERENCE_SPAN_KIND) == OpenInferenceSpanKindValues.LLM.value
        ):
            span._attributes = {**attrs, SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value}


class _ConversationSpan:
    def __init__(self, span):
        self._span = span

    @property
    def trace_id(self) -> str:
        return f"{self._span.get_span_context().trace_id:032x}"

    def set_output(self, answer: str) -> None:
        self._span.set_attribute(SpanAttributes.OUTPUT_VALUE, answer)


@contextlib.contextmanager
def traced_conversation(question: str, model_name: str, user_id: str, session_id: str):
    """Wrap one gateway request in a span with OpenInference input/output
    attributes, so `evals/phoenix_evals.py` has one predictable row per
    conversation turn regardless of how many tool calls or sub-agent delegations
    happened underneath.

    Call `.set_output(answer)` on the yielded handle once the final answer text is
    known — for a streaming response that means after the stream finishes, not
    before it starts, since OpenInference's OUTPUT_VALUE is a single string, not an
    incremental one.
    """
    with _tracer.start_as_current_span("chat_completion") as span:
        span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, OpenInferenceSpanKindValues.AGENT.value)
        span.set_attribute(SpanAttributes.INPUT_VALUE, question)
        span.set_attribute("gen_ai.request.model", model_name)
        span.set_attribute(SpanAttributes.USER_ID, user_id)
        # Phoenix's Sessions tab groups traces by this attribute — without it every
        # turn of a multi-turn chat shows up as an unrelated trace.
        span.set_attribute(SpanAttributes.SESSION_ID, session_id)
        yield _ConversationSpan(span)
