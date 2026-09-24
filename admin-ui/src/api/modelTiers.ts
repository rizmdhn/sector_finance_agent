// TEMPORARY mock backend for the model-tiering admin panel.
//
// There is no real admin API yet — gateway/roles/orchestrator.py's ROLE_TIERS is
// still a hardcoded dict edited by hand (see PROGRESS.md item 28). This module
// exists so the UI can be built and demoed against the *shape* that real API is
// expected to take, and swapped for real `fetch` calls later with no change to
// any component: `getModelTiering()` -> `GET /admin/model-tiers`, `updateRoleTier()`
// -> `PATCH /admin/model-tiers/{role}`. State persists to localStorage only so a
// page refresh doesn't lose a demo — it is not a substitute for the real backend
// (each browser has its own copy, nothing is shared, nothing reaches the gateway).
//
// ROLES and MODELS below mirror gateway/roles/orchestrator.py's ROLE_TIERS keys
// and models.yaml's entries as of PROGRESS.md item 28. Keep them in sync by hand
// until a real endpoint can serve this list directly from the registry.

import type { ModelInfo, ModelTieringState, RoleInfo, RoleTierConfig, Tier } from "../types";

const STORAGE_KEY = "idx-admin-ui.model-tiers.v1";
const MOCK_LATENCY_MS = 350;

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

export const MODELS: ModelInfo[] = [
  { name: "idx-analyst-claude", provider: "anthropic", tier: "cheap", usable: true },
  { name: "idx-analyst-gpt", provider: "openai", tier: "standard", usable: false },
  { name: "idx-analyst-claude-strong", provider: "anthropic", tier: "strong", usable: false },
];

const DEFAULT_CONFIG: RoleTierConfig = Object.fromEntries(ROLES.map((role) => [role.id, "cheap" as Tier]));

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function loadConfig(): RoleTierConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    const parsed = JSON.parse(raw) as RoleTierConfig;
    return { ...DEFAULT_CONFIG, ...parsed };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

function saveConfig(config: RoleTierConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {
    // Private browsing / storage disabled — the mock just won't persist across reloads.
  }
}

/** Mirrors gateway/registry.py's select_for_tier: pick a usable model of the
 * requested tier, falling back to the cheap/default model when the tier has no
 * real (non-placeholder) model registered yet. */
export function resolveModelForTier(tier: Tier): ModelInfo {
  const match = MODELS.find((model) => model.tier === tier && model.usable);
  if (match) return match;
  return MODELS.find((model) => model.tier === "cheap")!;
}

export async function getModelTiering(): Promise<ModelTieringState> {
  await sleep(MOCK_LATENCY_MS);
  return { roles: ROLES, models: MODELS, config: loadConfig() };
}

export async function updateRoleTier(roleId: string, tier: Tier): Promise<RoleTierConfig> {
  await sleep(MOCK_LATENCY_MS);
  const config = loadConfig();
  config[roleId] = tier;
  saveConfig(config);
  return config;
}
