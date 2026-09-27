// Real backend — gateway/main.py's /v1/memory and /v1/memory/settings endpoints.

import { apiFetch } from "./client";
import type { MemoryEntry, MemorySettings } from "../types";

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

export async function updateMemory(user: string, id: number, content: string): Promise<MemoryEntry> {
  return apiFetch<MemoryEntry>(`/v1/memory/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ user, content }),
  });
}

export async function deleteMemory(user: string, id: number): Promise<void> {
  await apiFetch<{ deleted: boolean }>(`/v1/memory/${id}?user=${encodeURIComponent(user)}`, {
    method: "DELETE",
  });
}

// AgentCore-style automatic extraction toggle (gateway/memory_extraction.py) —
// off by default; turning it on means every conversation triggers an extra
// Anthropic call (and, per fact written, a second cheap one to decide
// ADD/UPDATE/SKIP against similar existing facts) beyond the conversation
// itself.

export async function getMemorySettings(user: string): Promise<MemorySettings> {
  return apiFetch<MemorySettings>(`/v1/memory/settings?user=${encodeURIComponent(user)}`);
}

export async function updateMemorySettings(user: string, autoExtraction: boolean): Promise<MemorySettings> {
  return apiFetch<MemorySettings>("/v1/memory/settings", {
    method: "PATCH",
    body: JSON.stringify({ user, auto_extraction: autoExtraction }),
  });
}
