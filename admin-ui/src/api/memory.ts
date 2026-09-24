// Real backend — gateway/main.py's /v1/memory endpoints (added alongside this UI,
// see PROGRESS.md item 29), unlike src/api/modelTiers.ts and src/api/chat.ts which
// are still mocks.

import { apiFetch } from "./client";
import type { MemoryEntry } from "../types";

export async function listMemory(user: string): Promise<MemoryEntry[]> {
  const result = await apiFetch<{ data: MemoryEntry[] }>(`/v1/memory?user=${encodeURIComponent(user)}`);
  return result.data;
}

export async function addMemory(user: string, content: string): Promise<MemoryEntry> {
  return apiFetch<MemoryEntry>("/v1/memory", {
    method: "POST",
    body: JSON.stringify({ user, content }),
  });
}

export async function deleteMemory(user: string, id: number): Promise<void> {
  await apiFetch<{ deleted: boolean }>(`/v1/memory/${id}?user=${encodeURIComponent(user)}`, {
    method: "DELETE",
  });
}
