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

export type RoleTierConfig = Record<string, Tier>;

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
