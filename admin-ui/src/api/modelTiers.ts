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

/** Mirrors gateway/roles/orchestrator.py's model_for(): a role's stored
 * `choice` is checked against real model names FIRST (a direct pick, e.g.
 * "idx-analyst-gpt" regardless of its tier), then falls back to tier
 * resolution (gateway/registry.py's select_for_tier — a usable model of that
 * tier, or the cheap/default model if none exists for it). */
export function resolveChoice(choice: string, models: ModelInfo[]): ModelInfo {
  const direct = models.find((model) => model.name === choice && model.usable);
  if (direct) return direct;
  const tierMatch = models.find((model) => model.tier === choice && model.usable);
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
