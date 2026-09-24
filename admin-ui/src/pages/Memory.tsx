import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { addMemory, deleteMemory, listMemory, updateMemory } from "../api/memory";
import type { MemoryEntry } from "../types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function Memory({ userId }: { userId: string }) {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      setEntries(await listMemory(userId));
    } catch (err) {
      setError(err instanceof ApiError ? `Gateway error (${err.status}): ${err.message}` : "Could not reach the gateway.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    setAdding(true);
    try {
      const created = await addMemory(userId, draft.trim());
      setEntries((prev) => [created, ...prev]);
      setDraft("");
    } catch (err) {
      setError(err instanceof ApiError ? `Could not save (${err.status}): ${err.message}` : "Could not save.");
    } finally {
      setAdding(false);
    }
  }

  function startEdit(entry: MemoryEntry) {
    setEditingId(entry.id);
    setEditDraft(entry.content);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft("");
  }

  async function handleSaveEdit(id: number) {
    if (!editDraft.trim()) return;
    setSavingEdit(true);
    try {
      const updated = await updateMemory(userId, id, editDraft.trim());
      setEntries((prev) => prev.map((entry) => (entry.id === id ? updated : entry)));
      cancelEdit();
    } catch (err) {
      setError(err instanceof ApiError ? `Could not update (${err.status}): ${err.message}` : "Could not update.");
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      await deleteMemory(userId, id);
      setEntries((prev) => prev.filter((entry) => entry.id !== id));
    } catch (err) {
      setError(err instanceof ApiError ? `Could not delete (${err.status}): ${err.message}` : "Could not delete.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Memory</h1>
        <p className="subtitle">
          Long-term facts saved about <strong>{userId}</strong> — real data from Postgres
          (<code>user_memory</code>), the same store the Chief's <code>search_memory</code>/
          <code>add_memory</code> tools read and write mid-conversation. Add, edit, or remove a fact
          here and it's visible to the agent on its next chat immediately.
        </p>
      </header>

      <form className="memory-form" onSubmit={handleAdd}>
        <input
          className="memory-input"
          type="text"
          placeholder='e.g. "User holds 1000 shares of BBCA, cash 5,000,000 IDR"'
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={adding}
        />
        <button className="memory-add-btn" type="submit" disabled={adding || !draft.trim()}>
          {adding ? "Saving…" : "Add fact"}
        </button>
      </form>

      {loading && <p className="status-line">Loading…</p>}
      {error && <p className="status-line status-line--error">{error}</p>}

      {!loading && !error && entries.length === 0 && (
        <p className="empty-note">No facts saved yet for this user.</p>
      )}

      {!loading && entries.length > 0 && (
        <ul className="memory-list">
          {entries.map((entry) => {
            const isEditing = editingId === entry.id;
            return (
              <li key={entry.id} className="memory-item">
                {isEditing ? (
                  <div className="memory-item-main">
                    <input
                      className="memory-input memory-edit-input"
                      type="text"
                      value={editDraft}
                      onChange={(event) => setEditDraft(event.target.value)}
                      disabled={savingEdit}
                      autoFocus
                    />
                    <div className="memory-edit-actions">
                      <button
                        className="memory-add-btn"
                        onClick={() => handleSaveEdit(entry.id)}
                        disabled={savingEdit || !editDraft.trim()}
                      >
                        {savingEdit ? "Saving…" : "Save"}
                      </button>
                      <button className="memory-delete-btn" onClick={cancelEdit} disabled={savingEdit}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="memory-item-main">
                      <p className="memory-content">{entry.content}</p>
                      <div className="memory-meta">
                        {Boolean(entry.metadata?.kind) && (
                          <span className="kind-chip">{String(entry.metadata?.kind)}</span>
                        )}
                        <span className="memory-date">{formatDate(entry.created_at)}</span>
                      </div>
                    </div>
                    <div className="memory-item-actions">
                      <button className="memory-edit-btn" onClick={() => startEdit(entry)}>
                        Edit
                      </button>
                      <button
                        className="memory-delete-btn"
                        onClick={() => handleDelete(entry.id)}
                        disabled={deletingId === entry.id}
                      >
                        {deletingId === entry.id ? "Removing…" : "Remove"}
                      </button>
                    </div>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <p className="mock-note">Real endpoint: gateway/main.py's /v1/memory (GET/POST/PATCH/DELETE) — not a mock.</p>
    </div>
  );
}
