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
    """
    for entry in registry.values():
        if entry.tier == tier and entry.supports_tools and entry.model_id != PLACEHOLDER_MODEL_ID:
            return entry
    return default


def build_model(entry: ModelEntry) -> Model:
    """Return a Strands model instance for this registry entry."""
    api_key = os.environ[entry.key_ref]

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

        return AnthropicModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
            max_tokens=4096,
            cache_config=CacheConfig(strategy="anthropic", system_prompt_ttl=True, tools_ttl=True),
            params=params,
        )

    if entry.provider == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
        )

    raise ValueError(f"unsupported provider: {entry.provider}")
