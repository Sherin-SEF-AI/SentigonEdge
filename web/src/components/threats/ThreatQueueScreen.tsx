"use client";

import { useQuery } from "@tanstack/react-query";

import { playbookActivity, threatQueue, type Threat } from "@/lib/api";
import { useUI } from "@/store/ui";

const PRIORITY_COLOR: Record<string, string> = {
  P1: "text-red border-red/50 bg-red/10",
  P2: "text-amber border-amber/50 bg-amber/10",
  P3: "text-cyan border-cyan/40 bg-cyan/5",
  P4: "text-fg-muted border-line bg-panel",
};

function scoreBar(score: number): string {
  if (score >= 80) return "bg-red";
  if (score >= 60) return "bg-amber";
  if (score >= 40) return "bg-cyan";
  return "bg-fg-muted";
}

function hhmmss(ts?: string | null): string {
  return ts ? ts.slice(11, 19) : "--";
}

function ThreatRow({ t }: { t: Threat }) {
  const select = useUI((s) => s.select);
  const setTool = useUI((s) => s.setTool);
  const b = t.score_breakdown ?? {};
  const contribs = [
    ["sev", b.base_severity],
    ["cat", b.category],
    ["zone", b.zone],
    ["conf", b.confidence],
    ["vlm", b.verdict],
    ["corr", b.corroboration],
  ].filter(([, v]) => v !== undefined && v !== 0) as [string, number][];

  return (
    <button
      onClick={() => {
        select({ kind: "incident", id: t.id, label: t.title });
        setTool("reconstruction");
      }}
      className="flex w-full items-stretch gap-3 border-b border-line/50 px-3 py-2 text-left hover:bg-panel"
    >
      <div className={`mono flex w-10 shrink-0 flex-col items-center justify-center rounded-[3px] border text-[11px] ${PRIORITY_COLOR[t.priority] ?? PRIORITY_COLOR.P4}`}>
        <span className="font-semibold">{t.priority}</span>
        <span className="text-[13px]">{t.risk_score}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[12px] text-fg">{t.title}</span>
          {t.occurrence_count > 1 && (
            <span className="mono shrink-0 rounded-[2px] bg-raised px-1 text-[9px] text-fg-secondary">
              ×{t.occurrence_count}
            </span>
          )}
          {t.corroborating_signals > 0 && (
            <span className="mono shrink-0 rounded-[2px] bg-magenta/20 px-1 text-[9px] uppercase text-magenta">
              +{t.corroborating_signals} signal
            </span>
          )}
          {t.status === "escalated" && (
            <span className="mono shrink-0 rounded-[2px] bg-red/20 px-1 text-[9px] uppercase text-red">esc</span>
          )}
        </div>
        <div className="mono mt-0.5 flex items-center gap-2 text-[10px] text-fg-muted">
          <span>{t.signature}</span>
          <span>·</span>
          <span>{t.camera ?? "?"}</span>
          <span>·</span>
          <span>{hhmmss(t.created_at)}</span>
          {t.verdict && <span className="text-green">· {t.verdict}</span>}
        </div>
        {/* score contribution bar */}
        <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-raised">
          <div className={`h-full ${scoreBar(t.risk_score)}`} style={{ width: `${t.risk_score}%` }} />
        </div>
        <div className="mono mt-0.5 flex flex-wrap gap-x-2 text-[9px] text-fg-muted">
          {contribs.map(([k, v]) => (
            <span key={k}>
              {k} {v > 0 ? "+" : ""}{v}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}

export function ThreatQueueScreen() {
  const threatsQ = useQuery({
    queryKey: ["threats"],
    queryFn: ({ signal }) => threatQueue(0, signal),
    refetchInterval: 5000,
  });
  const sopQ = useQuery({
    queryKey: ["playbook-activity"],
    queryFn: ({ signal }) => playbookActivity(20, signal),
    refetchInterval: 8000,
  });

  const threats = threatsQ.data?.threats ?? [];
  const bands = threats.reduce<Record<string, number>>((a, t) => {
    a[t.priority] = (a[t.priority] ?? 0) + 1;
    return a;
  }, {});

  return (
    <div className="flex h-full overflow-hidden">
      {/* prioritized queue */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-3 py-2">
          <div>
            <div className="text-[13px] font-semibold text-fg">Threat Queue</div>
            <div className="text-[11px] text-fg-muted">Open threats ranked by composite risk score.</div>
          </div>
          <div className="mono flex gap-2 text-[10px]">
            {(["P1", "P2", "P3", "P4"] as const).map((p) => (
              <span key={p} className={`rounded-[2px] border px-1.5 py-0.5 ${PRIORITY_COLOR[p]}`}>
                {p} {bands[p] ?? 0}
              </span>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-auto">
          {threatsQ.isLoading && <div className="p-3 text-[12px] text-fg-muted">loading threats...</div>}
          {!threatsQ.isLoading && threats.length === 0 && (
            <div className="mono p-3 text-[12px] text-fg-muted">no open threats</div>
          )}
          {threats.map((t) => (
            <ThreatRow key={t.id} t={t} />
          ))}
        </div>
      </div>

      {/* SOP response activity */}
      <div className="w-[300px] shrink-0 overflow-auto border-l border-line">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">SOP Response Activity</div>
          <div className="text-[11px] text-fg-muted">Automated playbook executions.</div>
        </div>
        {(sopQ.data?.activity ?? []).map((a, i) => (
          <div key={i} className="border-b border-line/40 px-3 py-2">
            <div className="flex items-center justify-between">
              <span className="text-[12px] text-cyan">{a.playbook}</span>
              <span className="mono text-[10px] text-fg-muted">{hhmmss(a.ts)}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              {a.actions.map((act, j) => (
                <span
                  key={j}
                  className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[9px] uppercase text-fg-secondary"
                >
                  {act.startsWith("case:") ? "case opened" : act}
                </span>
              ))}
            </div>
          </div>
        ))}
        {(sopQ.data?.activity ?? []).length === 0 && (
          <div className="mono px-3 py-2 text-[11px] text-fg-muted">no SOP actions yet</div>
        )}
      </div>
    </div>
  );
}
