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
        )
        entries[entry.name] = entry
    return entries


def build_model(entry: ModelEntry) -> Model:
    """Return a Strands model instance for this registry entry."""
    api_key = os.environ[entry.key_ref]

    if entry.provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
            max_tokens=4096,
        )

    if entry.provider == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"api_key": api_key},
            model_id=entry.model_id,
        )

    raise ValueError(f"unsupported provider: {entry.provider}")
