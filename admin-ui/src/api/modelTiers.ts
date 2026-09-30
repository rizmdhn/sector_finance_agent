// Real backend — gateway/main.py's /v1/admin/model-tiers endpoints (GET/PATCH),
// backed by Postgres (data/schema.sql's role_tier_config, keyed by (user_id,
// role_id) — each user gets their own tiering, same scoping as /v1/memory — read
// fresh on every build_agent() call, so a PATCH takes effect on THAT user's very
// next request, no redeploy). Confirmed live: two different users given
// different tiers for the same role produced two genuinely different
// build_agent() model_ids (see PROGRESS.md item 38).
//
// ROLES is still static frontend metadata (labels/descriptions) — the backend
// only knows role ids, not display copy, and that's a reasonable split; keep it
// in sync with gateway/roles/orchestrator.py's DEFAULT_ROLE_TIERS keys by hand.
// `models` (unlike the old mock's hardcoded MODELS list) now comes straight from
// the real registry every time, so it can't drift out of sync with models.yaml.

import { apiFetch } from "./client";
import type { ModelInfo, ModelTieringState, RoleInfo, RoleTierConfig } from "../types";

export const ROLES: RoleInfo[] = [
  {
    id: "chief",
    label: "Chief Portfolio Intelligence Orchestrator",
    description: "Defines the question, delegates to specialists, synthesizes the final answer.",
  },
  {
    id: "investment_research_lead",
    label: "Investment Research Lead",
    description: "Company economics, financial quality, valuation, investment thesis.",
  },
  {
    id: "portfolio_risk_lead",
    label: "Portfolio Risk Lead",
    description: "Exposure, concentration, liquidity, and returns for a set of holdings.",
  },
  {
    id: "market_and_event_intelligence_lead",
    label: "Market and Event Intelligence Lead",
    description: "Unusual moves, foreign flow, broker activity, filings, corporate events.",
  },
  {
    id: "independent_risk_and_evidence_officer",
    label: "Independent Risk and Evidence Officer",
    description: "Reviews a draft answer's evidence and calculations; cannot be overruled.",
  },
];

// Every request in this app asks for this exact model at the top level
// (src/api/chat.ts's CHAT_MODEL) — per-role tiering overrides FROM there, same
// as gateway/registry.py::select_for_tier's `default` parameter. Hardcoded
// here rather than threaded through as a prop because it's genuinely the same
// constant on both sides of one hand-maintained contract, not a value that
// varies per call site.
const DEFAULT_MODEL_NAME = "idx-analyst-claude";

/** Mirrors gateway/registry.py::select_for_tier exactly, including the
 * 2026-09-30 key-awareness fix: a same-tier default with no configured key is
 * skipped in favor of another tier match that actually has one, instead of
 * being returned regardless. */
function selectForTier(tier: string, models: ModelInfo[]): ModelInfo {
  const default_ = models.find((m) => m.name === DEFAULT_MODEL_NAME)!;
  if (default_.tier === tier && default_.key_configured) return default_;
  const match = models.find((m) => m.tier === tier && m.usable && m.key_configured);
  if (match) return match;
  return default_;
}

/** Walks a model's registered `fallback` chain, mirroring gateway/main.py's
 * _build_agent_with_fallback — an explicit by-name pick with no key isn't
 * corrected by selectForTier (that only applies to tier choices), so without
 * this the preview would show a model as "live" that will actually fail on
 * the first try and only recover via this exact chain. `seen` guards a cycle
 * the same way the backend does. */
function walkFallback(entry: ModelInfo, models: ModelInfo[], seen = new Set<string>()): ModelInfo {
  if (entry.key_configured || !entry.fallback || seen.has(entry.name)) return entry;
  seen.add(entry.name);
  const next = models.find((m) => m.name === entry.fallback);
  if (!next) return entry;
  return walkFallback(next, models, seen);
}

/** Mirrors gateway/roles/orchestrator.py's model_for(): a role's stored
 * `choice` is checked against real model names FIRST (a direct pick, e.g.
 * "idx-analyst-gpt" regardless of its tier), then falls back to tier
 * resolution (gateway/registry.py's select_for_tier — a usable model of that
 * tier, or the cheap/default model if none exists for it). */
export function resolveChoice(choice: string, models: ModelInfo[]): ModelInfo {
  const direct = models.find((model) => model.name === choice && model.usable);
  if (direct) return walkFallback(direct, models);
  const tierMatch = ["cheap", "standard", "strong"].includes(choice) ? selectForTier(choice, models) : undefined;
  if (tierMatch) return tierMatch;
  return models.find((model) => model.tier === "cheap")!;
}

export async function getModelTiering(userId: string): Promise<ModelTieringState> {
  const result = await apiFetch<{ config: RoleTierConfig; models: ModelInfo[] }>(
    `/v1/admin/model-tiers?user=${encodeURIComponent(userId)}`
  );
  return { roles: ROLES, models: result.models, config: result.config };
}

export async function updateRoleTier(userId: string, roleId: string, choice: string): Promise<void> {
  await apiFetch<{ user: string; role: string; tier: string }>(`/v1/admin/model-tiers/${roleId}`, {
    method: "PATCH",
    body: JSON.stringify({ user: userId, tier: choice }),
  });
}
