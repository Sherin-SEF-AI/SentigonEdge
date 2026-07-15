"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { core, type ModelVersion } from "@/lib/api";
import { useAuth } from "@/store/auth";

const ROLE_LABEL: Record<string, string> = {
  detector: "Object Detection",
  vlm: "Vision-Language Reasoning",
  reid: "Re-Identification",
  embed: "Embedding / Search",
};

const STAGE_STYLE: Record<string, string> = {
  champion: "bg-green/15 text-green border-green/40",
  challenger: "bg-amber/15 text-amber border-amber/40",
  retired: "bg-raised text-fg-muted border-line",
};

function fmt(k: string, v: number): string {
  if (k.includes("rate") || k.includes("reduction")) return `${(v * 100).toFixed(1)}%`;
  if (k.startsWith("mAP") || k === "precision" || k === "recall") return v.toFixed(3);
  return String(v);
}

function Metric({ k, v }: { k: string; v: number }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-fg-muted">{k.replace(/_/g, " ")}</span>
      <span className="mono text-[12px] text-fg">{fmt(k, v)}</span>
    </div>
  );
}

function PerClass({ pc }: { pc: Record<string, { ap50: number; precision: number; recall: number }> }) {
  const rows = Object.entries(pc).sort((a, b) => b[1].ap50 - a[1].ap50);
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-[10px] uppercase tracking-wide text-fg-muted">
        per-class ({rows.length})
      </summary>
      <div className="mt-1 space-y-0.5">
        <div className="mono flex justify-between text-[9px] uppercase text-fg-muted">
          <span>class</span>
          <span>AP50 / P / R</span>
        </div>
        {rows.map(([name, m]) => (
          <div key={name} className="mono flex justify-between text-[10px]">
            <span className="text-fg-secondary">{name}</span>
            <span className="text-fg">
              {m.ap50.toFixed(2)} / {m.precision.toFixed(2)} / {m.recall.toFixed(2)}
            </span>
          </div>
        ))}
      </div>
    </details>
  );
}

function EvalBlock({ ev }: { ev: Record<string, unknown> }) {
  const perClass = ev.per_class as
    | Record<string, { ap50: number; precision: number; recall: number }>
    | undefined;
  const gold = typeof ev.gold_set === "string" ? ev.gold_set : null;
  const scalars = Object.entries(ev).filter(
    ([k, v]) => typeof v === "number" && k !== "images" && k !== "classes_evaluated",
  ) as [string, number][];
  return (
    <div className="mb-2 space-y-0.5 border-t border-line pt-2">
      {gold && (
        <div className="mb-1 text-[10px] uppercase tracking-wide text-fg-muted">gold set: {gold}</div>
      )}
      {scalars.map(([k, v]) => (
        <Metric key={k} k={k} v={v} />
      ))}
      {perClass && <PerClass pc={perClass} />}
    </div>
  );
}

function ModelCard({ m, onPromote, busy }: { m: ModelVersion; onPromote: () => void; busy: boolean }) {
  return (
    <div className="border border-line bg-panel p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-fg">{m.name}</span>
        <span className={`mono rounded-[2px] border px-1.5 py-0.5 text-[10px] uppercase ${STAGE_STYLE[m.stage] ?? ""}`}>
          {m.stage}
        </span>
      </div>
      <div className="mono mb-2 text-[11px] text-fg-muted">{m.artifact_ref}</div>
      {m.latest_eval ? (
        <EvalBlock ev={m.latest_eval} />
      ) : (
        <div className="mb-2 border-t border-line pt-2 text-[11px] text-fg-muted">
          no eval yet {m.role === "detector" ? "(needs labeled gold set)" : ""}
        </div>
      )}
      {m.stage === "challenger" && (
        <button
          disabled={busy}
          onClick={onPromote}
          className="mono w-full rounded-[3px] bg-raised py-1 text-[11px] text-cyan hover:bg-base disabled:opacity-40"
        >
          {busy ? "promoting..." : "promote to champion"}
        </button>
      )}
      {m.promoted_at && m.stage === "champion" && (
        <div className="text-[10px] text-fg-muted">promoted {new Date(m.promoted_at).toLocaleString()}</div>
      )}
    </div>
  );
}

export function ModelsScreen() {
  const qc = useQueryClient();
  const user = useAuth((s) => s.user);
  const setModal = useAuth((s) => s.setModal);
  const q = useQuery({ queryKey: ["models"], queryFn: ({ signal }) => core.models(signal), refetchInterval: 8000 });

  const register = useMutation({
    mutationFn: () => core.registerModels(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e: Error) => {
      if (e.message.startsWith("401")) setModal(true);
    },
  });
  const promote = useMutation({
    mutationFn: (id: string) => core.promoteModel(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["models"] }),
    onError: (e: Error) => {
      if (e.message.startsWith("401")) setModal(true);
    },
  });

  const models = q.data ?? [];
  const roles = Array.from(new Set(models.map((m) => m.role)));

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <div>
          <div className="text-[13px] font-semibold text-fg">Model Governance</div>
          <div className="text-[11px] text-fg-muted">
            Champion / challenger registry. Promotion is atomic and audited.
          </div>
        </div>
        <button
          disabled={register.isPending}
          onClick={() => register.mutate()}
          className="mono rounded-[3px] border border-line bg-panel px-3 py-1.5 text-[11px] text-cyan hover:bg-raised disabled:opacity-40"
        >
          {register.isPending ? "registering..." : "register running models"}
        </button>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {!user && (
          <div className="mb-3 border border-amber/40 bg-amber/10 px-3 py-2 text-[12px] text-amber">
            Sign in as operator+ to register or promote models.
          </div>
        )}
        {models.length === 0 ? (
          <div className="mono py-10 text-center text-[12px] text-fg-muted">
            No models registered. Click &ldquo;register running models&rdquo;.
          </div>
        ) : (
          <div className="space-y-6">
            {roles.map((role) => (
              <div key={role}>
                <div className="mono mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
                  {ROLE_LABEL[role] ?? role}
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {models
                    .filter((m) => m.role === role)
                    .map((m) => (
                      <ModelCard
                        key={m.id}
                        m={m}
                        busy={promote.isPending && promote.variables === m.id}
                        onPromote={() => promote.mutate(m.id)}
                      />
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
