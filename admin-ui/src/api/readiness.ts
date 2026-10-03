// Real backend — gateway/main.py's GET /v1/admin/readiness. Lets the UI gate
// itself behind a "still setting up" screen on a fresh deploy instead of letting
// a user hit a confusing "unknown symbol" error while ingest-worker's one-time
// data seed (ingest/scheduler.py) is still running.

import { apiFetch } from "./client";
import type { Problem } from "./problems";

export interface ReadinessState {
  ready: boolean;
  symbol_master_ready: boolean;
  price_data_ready: boolean;
  model_key_ready: boolean;
  problems: Problem[];
  // e.g. "Rate limited by Sectors — retrying in 60s" while the first ticker load is backing off
  ingest_note: string | null;
}

export async function getReadiness(): Promise<ReadinessState> {
  return apiFetch<ReadinessState>("/v1/admin/readiness");
}
