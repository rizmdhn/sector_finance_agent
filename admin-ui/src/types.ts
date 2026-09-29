// Mirrors gateway/registry.py (ModelEntry) and gateway/roles/orchestrator.py
// (ROLE_TIERS) on the Python side. Keep this in sync by hand for now — there is
// no shared schema yet (see src/api/modelTiers.ts's header comment).

export type Tier = "cheap" | "standard" | "strong";

export interface ModelInfo {
  name: string;
  provider: string;
  tier: Tier;
  /** True once model_id is a real provider id, not models.yaml's "TODO" placeholder. */
  usable: boolean;
}

export interface RoleInfo {
  /** Matches gateway/roles/orchestrator.py's ROLE_TIERS keys exactly. */
  id: string;
  label: string;
  description: string;
}

// A role's stored choice is either a Tier keyword or a real ModelInfo.name
// picked directly (gateway/roles/orchestrator.py::model_for checks the
// registry before falling back to tier resolution) — not just Tier anymore.
export type RoleTierConfig = Record<string, string>;

export interface ModelTieringState {
  roles: RoleInfo[];
  models: ModelInfo[];
  config: RoleTierConfig;
}

// -- Memory (real: backed by gateway/main.py's /v1/memory endpoints) ----------

export interface MemoryEntry {
  id: number;
  content: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface MemorySettings {
  auto_extraction: boolean;
}

// -- Evals (real: gateway/main.py's /v1/admin/evals endpoints, gateway/eval_runner.py) --

export type EvalRunStatus = "pending" | "running" | "done" | "error" | "cancelled";

export interface EvalClassifierResult {
  /** Count of graded conversations per raw judge label. */
  tally: Record<string, number>;
  /** The one label that counts as this rule being followed. */
  pass_label: string;
  /** pass_label count / (graded - not_applicable) — the "accuracy" shown in the
   * UI. Null when every graded conversation came back not_applicable. */
  pass_rate: number | null;
}

export interface EvalRunSummary {
  conversations: number;
  /** Per classifier name (evals/phoenix_evals.py's CLASSIFIERS keys). */
  classifiers: Record<string, EvalClassifierResult>;
}

export interface EvalRun {
  id: number;
  user_id: string;
  judge_provider: string;
  judge_model: string;
  status: EvalRunStatus;
  cancel_requested: boolean;
  total: number;
  completed: number;
  summary: EvalRunSummary | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

// -- Chat (mock only — see src/api/chat.ts's header comment) ------------------

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

export interface ChatSession {
  id: string;
  userId: string;
  title: string;
  createdAt: string;
  messages: ChatMessage[];
}
