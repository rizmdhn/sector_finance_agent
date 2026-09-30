import { useEffect, useMemo, useState } from "react";
import { getModelTiering, resolveChoice, updateRoleTier } from "../api/modelTiers";
import type { ModelInfo, RoleInfo, RoleTierConfig, Tier } from "../types";

const TIERS: Tier[] = ["cheap", "standard", "strong"];

const TIER_BLURB: Record<Tier, string> = {
  cheap: "Fast, low cost. Right for routine coordination and most specialist work.",
  standard: "A step up in reasoning, a step up in cost per call.",
  strong: "Most capable, most expensive. Reserve for the hardest judgment calls.",
};

type SaveState = "idle" | "saving" | "saved" | "error";

function ResolvedModel({ choice, models }: { choice: string; models: ModelInfo[] }) {
  const isDirectPick = models.some((m) => m.name === choice);
  const requested = isDirectPick ? models.find((m) => m.name === choice) : models.find((m) => m.tier === choice);
  const resolved = resolveChoice(choice, models);

  // "Live" means EXACTLY what you picked will run, no substitution — checked
  // by name, not just `usable` (a real model_id): `usable` says nothing about
  // whether that model's own API key is actually configured. Real bug found
  // live (2026-09-30): this used to show a confident green "live" dot on
  // idx-analyst-claude with only an OpenAI key set — the backend was already
  // routing everything to GPT instead (gateway/registry.py's key-aware
  // select_for_tier), so the UI was telling the user something different from
  // what would actually answer their question.
  const isLive = resolved.name === requested?.name && resolved.key_configured;

  if (isLive) {
    return (
      <div className="resolved resolved--live">
        <span className="resolved-dot" />
        <span className="resolved-name">{resolved.name}</span>
      </div>
    );
  }

  const reason = !requested?.usable
    ? `${choice} has no usable model registered`
    : !requested.key_configured
      ? `${requested.name}'s API key isn't configured on the gateway`
      : `${choice} has no usable model`;

  return (
    <div className="resolved resolved--fallback" title={reason}>
      <span className="resolved-dot" />
      <span>
        falls back to <span className="resolved-name">{resolved.name}</span>
      </span>
    </div>
  );
}

function RoleCard({
  role,
  choice,
  models,
  onChange,
  saveState,
}: {
  role: RoleInfo;
  choice: string;
  models: ModelInfo[];
  onChange: (roleId: string, choice: string) => void;
  saveState: SaveState;
}) {
  const usableModels = models.filter((m) => m.usable);
  return (
    <article className="role-card">
      <div className="role-card-head">
        <div>
          <h3 className="role-name">{role.label}</h3>
          <p className="role-desc">{role.description}</p>
        </div>
        {saveState === "saving" && <span className="save-state save-state--saving">saving…</span>}
        {saveState === "saved" && <span className="save-state save-state--saved">saved</span>}
        {saveState === "error" && <span className="save-state save-state--error">failed, retry</span>}
      </div>

      <select
        className="tier-select"
        aria-label={`Model or tier for ${role.label}`}
        value={choice}
        onChange={(event) => onChange(role.id, event.target.value)}
      >
        <optgroup label="Tier (auto-picks a model)">
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </optgroup>
        <optgroup label="Specific model">
          {usableModels.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name} ({m.provider})
            </option>
          ))}
        </optgroup>
      </select>

      <ResolvedModel choice={choice} models={models} />
    </article>
  );
}

export default function ModelTiering({ userId }: { userId: string }) {
  const [roles, setRoles] = useState<RoleInfo[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [config, setConfig] = useState<RoleTierConfig>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saveStateByRole, setSaveStateByRole] = useState<Record<string, SaveState>>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const state = await getModelTiering(userId);
        if (cancelled) return;
        setRoles(state.roles);
        setModels(state.models);
        setConfig(state.config);
      } catch {
        if (!cancelled) setError("Could not load model tiering config.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  const modelsByTier = useMemo(() => {
    const grouped: Record<Tier, ModelInfo[]> = { cheap: [], standard: [], strong: [] };
    for (const model of models) grouped[model.tier].push(model);
    return grouped;
  }, [models]);

  async function handleTierChange(roleId: string, choice: string) {
    setConfig((prev) => ({ ...prev, [roleId]: choice }));
    setSaveStateByRole((prev) => ({ ...prev, [roleId]: "saving" }));
    try {
      await updateRoleTier(userId, roleId, choice);
      setSaveStateByRole((prev) => ({ ...prev, [roleId]: "saved" }));
      setTimeout(() => {
        setSaveStateByRole((prev) => (prev[roleId] === "saved" ? { ...prev, [roleId]: "idle" } : prev));
      }, 1500);
    } catch {
      setSaveStateByRole((prev) => ({ ...prev, [roleId]: "error" }));
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Model Tiering</h1>
        <p className="subtitle">
          Assign each agent role a cost tier — or a specific model — for <strong>{userId}</strong>. Each user has
          their own config; a tier or model with nothing usable registered falls back to the cheap model.
        </p>
      </header>

      {loading && <p className="status-line">Loading…</p>}
      {error && <p className="status-line status-line--error">{error}</p>}

      {!loading && !error && (
        <>
          <div className="tier-legend">
            {TIERS.map((t) => (
              <div key={t} className="tier-legend-item">
                <span className="tier-legend-label">{t}</span>
                <span className="tier-legend-blurb">{TIER_BLURB[t]}</span>
              </div>
            ))}
          </div>

          <div className="role-grid">
            {roles.map((role) => (
              <RoleCard
                key={role.id}
                role={role}
                choice={config[role.id] ?? "cheap"}
                models={models}
                onChange={handleTierChange}
                saveState={saveStateByRole[role.id] ?? "idle"}
              />
            ))}
          </div>

          <section className="model-reference">
            <h2>Registered models by tier</h2>
            <div className="tier-columns">
              {TIERS.map((tier) => (
                <div key={tier} className="tier-column">
                  <h3>{tier}</h3>
                  {modelsByTier[tier].length === 0 && <p className="empty-note">none</p>}
                  {modelsByTier[tier].map((model) => (
                    <div
                      key={model.name}
                      className={`model-card ${model.usable && model.key_configured ? "" : "model-card--placeholder"}`}
                    >
                      <div className="model-card-name">{model.name}</div>
                      <div className="model-card-provider">{model.provider}</div>
                      {!model.usable && <div className="model-card-note">placeholder — not usable yet</div>}
                      {model.usable && !model.key_configured && (
                        <div className="model-card-note">no API key configured on the gateway</div>
                      )}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </section>

          <p className="mock-note">
            Real endpoint: gateway/main.py's /v1/admin/model-tiers (GET/PATCH), backed by Postgres. A change here
            applies to the next chat request — no redeploy needed.
          </p>
        </>
      )}
    </div>
  );
}
