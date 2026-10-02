// Real backend — gateway/main.py's GET /v1/admin/traces/{id}: one chat turn's agents,
// tools, tokens and Sectors credits, read back from Phoenix. Phoenix receives spans in
// batches, so a fresh trace can take a few seconds to appear (`ready: false`).

import { apiFetch } from "./client";

export interface TraceStep {
  kind: "AGENT" | "TOOL" | "LLM";
  name: string;
  depth: number;
  offset_ms: number;
  duration_ms: number;
  tokens: number;
  credits: number;
  error: boolean;
}

export interface TraceSummary {
  duration_ms: number;
  credits: number;
  tokens: number;
  llm_calls: number;
  steps: TraceStep[];
  phoenix_url: string | null;
}

const RETRY_MS = 2000;
const MAX_TRIES = 8;
const cache = new Map<string, Promise<TraceSummary | null>>();

// Resolves with the trace once Phoenix has it, or null if it never shows up. Shared by the
// per-reply credit chip and the open drawer, so one trace is fetched once.
export function fetchTraceWhenReady(traceId: string): Promise<TraceSummary | null> {
  let pending = cache.get(traceId);
  if (!pending) {
    pending = (async () => {
      for (let attempt = 0; attempt < MAX_TRIES; attempt++) {
        try {
          const result = await apiFetch<{ ready: boolean } & Partial<TraceSummary>>(`/v1/admin/traces/${traceId}`);
          if (result.ready) return result as TraceSummary;
        } catch {
          // Phoenix briefly unreachable — treated like "not ready yet"
        }
        await new Promise((resolve) => setTimeout(resolve, RETRY_MS));
      }
      return null;
    })();
    cache.set(traceId, pending);
    // Don't pin a failure: a later click should be allowed to try again.
    pending.then((value) => value ?? cache.delete(traceId));
  }
  return pending;
}
