import { useEffect, useMemo, useState } from "react";
import { getModelTiering, resolveModelForTier, updateRoleTier } from "../api/modelTiers";
import type { ModelInfo, RoleInfo, RoleTierConfig, Tier } from "../types";

const TIERS: Tier[] = ["cheap", "standard", "strong"];

const TIER_BLURB: Record<Tier, string> = {
  cheap: "Fast, low cost. Right for routine coordination and most specialist work.",
  standard: "A step up in reasoning, a step up in cost per call.",
  strong: "Most capable, most expensive. Reserve for the hardest judgment calls.",
};

type SaveState = "idle" | "saving" | "saved" | "error";

function ResolvedModel({ tier, models }: { tier: Tier; models: ModelInfo[] }) {
  const requested = models.find((m) => m.tier === tier);
  const resolved = resolveModelForTier(tier, models);

  if (requested?.usable) {
    return (
      <div className="resolved resolved--live">
        <span className="resolved-dot" />
        <span className="resolved-name">{resolved.name}</span>
      </div>
    );
  }
  return (
    <div
      className="resolved resolved--fallback"
      title={`${tier} has no real model registered yet — falls back to cheap`}
    >
      <span className="resolved-dot" />
      <span>
        falls back to <span className="resolved-name">{resolved.name}</span>
      </span>
    </div>
  );
}

function RoleCard({
  role,
  tier,
  models,
  onChange,
  saveState,
}: {
  role: RoleInfo;
  tier: Tier;
  models: ModelInfo[];
  onChange: (roleId: string, tier: Tier) => void;
  saveState: SaveState;
}) {
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

      <div className="tier-segmented" role="radiogroup" aria-label={`Tier for ${role.label}`}>
        {TIERS.map((t) => (
          <button
            key={t}
            type="button"
            role="radio"
            aria-checked={tier === t}
            className={`tier-segment ${tier === t ? "tier-segment--active" : ""}`}
            onClick={() => onChange(role.id, t)}
          >
            {t}
          </button>
        ))}
      </div>

      <ResolvedModel tier={tier} models={models} />
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

  async function handleTierChange(roleId: string, tier: Tier) {
    setConfig((prev) => ({ ...prev, [roleId]: tier }));
    setSaveStateByRole((prev) => ({ ...prev, [roleId]: "saving" }));
    try {
      await updateRoleTier(userId, roleId, tier);
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
          Assign each agent role a cost tier for <strong>{userId}</strong>. Each user has their own tiering — a tier
          with no usable model yet falls back to the cheap model.
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
                tier={config[role.id] ?? "cheap"}
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
                    <div key={model.name} className={`model-card ${model.usable ? "" : "model-card--placeholder"}`}>
                      <div className="model-card-name">{model.name}</div>
                      <div className="model-card-provider">{model.provider}</div>
                      {!model.usable && <div className="model-card-note">placeholder — not usable yet</div>}
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
