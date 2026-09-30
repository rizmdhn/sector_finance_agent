"""Model registry: loads models.yaml and builds the Strands model for a name.

See idx_agent_infrastructure_diagrams_md.md section 5.
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from strands.models.model import Model

DEFAULT_REGISTRY_PATH = Path(__file__).parent.parent / "models.yaml"


@dataclass(frozen=True)
class ModelEntry:
    name: str
    provider: str
    model_id: str
    key_ref: str
    context_limit: int
    supports_tools: bool
    tier: str
    fallback: str | None
    # Anthropic's `output_config.effort` ("low"..."max") — thinking depth/spend
    # within THIS model, not a different model. None omits the param entirely
    # (the API's own default applies) — required for Haiku 4.5, which errors if
    # `effort` is passed at all (confirmed via the claude-api skill's model
    # table, 2026-09-24). Only meaningful for `provider: anthropic`.
    effort: str | None = None


def load_registry(path: Path | str = DEFAULT_REGISTRY_PATH) -> dict[str, ModelEntry]:
    raw = yaml.safe_load(Path(path).read_text())
    entries = {}
    for item in raw["models"]:
        entry = ModelEntry(
            name=item["name"],
            provider=item["provider"],
            model_id=item["model_id"],
            key_ref=item["key_ref"],
            context_limit=item["context_limit"],
            supports_tools=item["supports_tools"],
            tier=item["tier"],
            fallback=item.get("fallback"),
            effort=item.get("effort"),
        )
        entries[entry.name] = entry
    return entries


PLACEHOLDER_MODEL_ID = "TODO"


def select_for_tier(registry: dict[str, ModelEntry], tier: str, default: ModelEntry) -> ModelEntry:
    """Pick a usable model entry of the given tier from the registry, falling back
    to `default` (the request's own model_entry) when no such entry is configured
    yet — e.g. models.yaml's `standard`/`strong` slots may still hold a placeholder
    model_id until there's Anthropic credit to spend on them (see PROGRESS.md's
    per-role model tiering entry). This keeps per-role tiering (gateway/roles/
    orchestrator.py's ROLE_TIERS) a no-op today: every role resolves back to
    `default` until a real model is registered for the tier it asks for.

    `default.tier == tier` short-circuits straight to `default` before ever
    scanning the registry — real bug found live (2026-09-28): with two entries
    sharing a tier (`idx-analyst-claude` and `idx-analyst-gpt` both `cheap`),
    every role defaulting to `cheap` silently resolved back to whichever entry
    happens to be declared first in models.yaml, regardless of which model the
    request actually asked for — picking `idx-analyst-gpt` at the top level had
    no effect at all, every role still ran on Claude. The whole point of this
    function is substituting a DIFFERENT tier's model in for a role that needs
    more/less capability than what was requested; when the role's tier already
    matches what was requested, there's nothing to substitute.
    """
    if default.tier == tier:
        return default
    for entry in registry.values():
        if entry.tier == tier and entry.supports_tools and entry.model_id != PLACEHOLDER_MODEL_ID:
            return entry
    return default


def build_model(entry: ModelEntry) -> Model:
    """Return a Strands model instance for this registry entry.

    Raises if `entry.key_ref` is unset OR set-but-blank (real live difference: a
    blank ANTHROPIC_API_KEY="" in .env makes `os.environ[...]` succeed with "",
    which is NOT the same failure as a missing key — `os.environ[entry.key_ref]`
    alone only catches the latter). Checked here, not left to surface from the
    provider SDK client, because BOTH anthropic.AsyncAnthropic(api_key="") and
    OpenAI(api_key="") construct successfully with no error at all — confirmed
    live, not assumed — the auth failure only happens on the first real network
    call, deep inside streaming. That call site is NOT wrapped by gateway/main.py's
    _build_agent_with_fallback (which only catches errors during THIS function/
    build_agent(), not during actual inference), so a blank key silently defeated
    every registry fallback chain (e.g. idx-analyst-claude -> idx-analyst-gpt) —
    real bug, found live: a user with only OPENAI_API_KEY set still hit a gateway
    error instead of automatically falling back to GPT, because nothing failed
    until it was too late for _build_agent_with_fallback to catch it. Raising here
    instead makes the failure happen at build time, inside the same try/except
    that already knows how to retry with entry.fallback — no change needed there.
    """
    api_key = os.environ.get(entry.key_ref) or ""
    if not api_key:
        raise RuntimeError(f"{entry.key_ref} is not set — cannot build model {entry.name!r}")

    if entry.provider == "anthropic":
        from strands.models.anthropic import AnthropicModel
        from strands.models.model import CacheConfig

        # Prompt caching, NOT answer caching: every call still gets a fresh, real
        # LLM response — only the repeated fixed prefix (system prompt + tool
        # schemas, both static per role) gets billed at Anthropic's cached-read
        # rate on every call after the first within the TTL, instead of full input
        # price. Confirmed live (see PROGRESS.md): a second identical-prefix call
        # showed real cache_read_input_tokens in its usage, at ~1/10th the cost of
        # an uncached prompt token. This is a much better fit than an answer cache
        # for "ask about the same company again" — it never risks serving a stale
        # or subtly-wrong cached answer, since the model always actually runs.
        # `params` passes straight through to the real Messages API request body
        # (confirmed via strands.models.anthropic.AnthropicModel's own docstring)
        # — `output_config.effort` here is the same field the Anthropic API docs
        # call `output_config.effort`, not a Strands-specific concept. Omitted
        # entirely when entry.effort is None, since Haiku 4.5 errors if the field
        # is present at all, even as a no-op default.
        params = {"output_config": {"effort": entry.effort}} if entry.effort else None

        # ttl="1h": Anthropic's extended cache TTL, not the 5-minute default a bare
        # system_prompt_ttl=True would fall back to. A chat session realistically
        # idles for minutes between messages — the 5-min window was expiring and
        # re-billing the full system prompt + tool schemas at uncached price on
        # most real turns, not just the first one per session. The 1h write costs
        # 2x a 5m write, but a cached read is still ~1/10th price either way, so
        # this pays for itself after the second hit within the hour, which a real
        # back-and-forth conversation clears easily.
        return AnthropicModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
            max_tokens=4096,
            cache_config=CacheConfig(strategy="anthropic", ttl="1h", system_prompt_ttl=True, tools_ttl=True),
            params=params,
        )

    if entry.provider == "openai":
        from strands.models.model import CacheConfig
        from strands.models.openai import OpenAIModel

        # Reuses the same `effort` YAML field as the Anthropic branch above, but
        # maps to a different real API param — `reasoning_effort`, OpenAI's own
        # name for it (confirmed via a live error, not guessed): a reasoning
        # model (gpt-5.6-luna) rejects tool calling on /v1/chat/completions
        # entirely unless `reasoning_effort` is explicitly "none" —
        # `openai.BadRequestError: Function tools with reasoning_effort are not
        # supported for gpt-5.6-luna in /v1/chat/completions.` Omitted when
        # entry.effort is None so a non-reasoning OpenAI model (no `effort` set
        # in models.yaml) doesn't get a param it never asked for.
        params = {"reasoning_effort": entry.effort} if entry.effort else None

        return OpenAIModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
            params=params,
            # OpenAI caches prompt prefixes automatically server-side but routes
            # cache reads on a caller-supplied `prompt_cache_key` — with none set
            # (the state before this), every request had no explicit routing hint
            # at all. A bare CacheConfig() derives `strands-<session_id>` per
            # Strands' own default, keeping one session's repeat calls routed to
            # the same cache partition. Free — OpenAI's own caching costs nothing
            # extra, this only improves the odds of actually hitting it.
            cache_config=CacheConfig(),
        )

    if entry.provider == "openai_responses":
        from strands.models.model import CacheConfig
        from strands.models.openai_responses import OpenAIResponsesModel

        # A separate provider, not just another `openai` entry with a different
        # `effort` value: found live (item 45) that gpt-6-astra can't do tool
        # calling on /v1/chat/completions AT ALL — it requires reasoning_effort
        # "none" for tools, same as gpt-5.6-luna/sol, but then rejects "none" as
        # a value for itself ("Supported values are: 'low', 'medium', 'high',
        # and 'xhigh'"), a genuine dead end on that endpoint. OpenAI's newer
        # Responses API (`client.responses.create`, Strands' separate
        # OpenAIResponsesModel) accepts a real reasoning effort alongside tools
        # — confirmed live with a raw `client.responses.create(...,
        # reasoning={"effort": "low"})` call before wiring this in. The
        # `reasoning` param shape (`{"effort": ...}`) is the Responses API's
        # own, different from Chat Completions' flat `reasoning_effort`.
        params = {"reasoning": {"effort": entry.effort}} if entry.effort else None

        return OpenAIResponsesModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
            params=params,
            cache_config=CacheConfig(),  # same reasoning as the `openai` branch above
        )

    raise ValueError(f"unsupported provider: {entry.provider}")
