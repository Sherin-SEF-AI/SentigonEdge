"use client";

import { useQuery } from "@tanstack/react-query";

import { core, zoneBaselines, type Incident, type ZoneBaseline } from "@/lib/api";
import { useUI } from "@/store/ui";

function hhmmss(ts?: string | null): string {
  return ts ? ts.slice(11, 19) : "--";
}

// position of current occupancy relative to baseline mean +- 3 std, clamped 0..100%
function markerPct(z: number | null): number {
  if (z === null) return 50;
  return Math.max(2, Math.min(98, 50 + (z / 3) * 48));
}

function BaselineRow({ b }: { b: ZoneBaseline }) {
  const anomalous = b.anomalous;
  return (
    <div className={`border px-3 py-2 ${anomalous ? "border-red/60 bg-red/5" : "border-line bg-panel"}`}>
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-fg">{b.zone}</span>
        <span className="mono text-[10px] text-fg-muted">
          {b.zone_type ?? ""} · {b.samples} samples
        </span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-[11px]">
        <div>
          <div className="text-[9px] uppercase text-fg-muted">learned normal</div>
          <div className="mono text-fg">
            {b.baseline_mean} <span className="text-fg-muted">± {b.baseline_std}</span>
          </div>
        </div>
        <div>
          <div className="text-[9px] uppercase text-fg-muted">current</div>
          <div className={`mono ${anomalous ? "text-red" : "text-fg"}`}>{b.current_occupancy ?? "--"}</div>
        </div>
        <div>
          <div className="text-[9px] uppercase text-fg-muted">deviation</div>
          <div className={`mono ${anomalous ? "text-red" : b.z_score !== null && b.z_score >= 2 ? "text-amber" : "text-fg-muted"}`}>
            {b.z_score === null ? "--" : `${b.z_score >= 0 ? "+" : ""}${b.z_score}σ`}
          </div>
        </div>
      </div>
      {/* deviation gauge: center = normal, right = above, red band beyond 3σ */}
      <div className="relative mt-2 h-2 w-full rounded-full bg-raised">
        <div className="absolute left-1/2 top-0 h-full w-px bg-fg-muted/50" />
        <div className="absolute right-0 top-0 h-full w-[16%] rounded-r-full bg-red/20" />
        <div
          className={`absolute top-[-2px] h-3 w-1 rounded-full ${anomalous ? "bg-red" : "bg-cyan"}`}
          style={{ left: `${markerPct(b.z_score)}%` }}
        />
      </div>
      {anomalous && (
        <div className="mono mt-1 text-[10px] text-red">
          ANOMALY: {b.current_occupancy} present vs learned {b.baseline_mean}
        </div>
      )}
      {!b.learned && (
        <div className="mono mt-1 text-[10px] text-fg-muted">learning baseline ({b.samples}/30 samples)</div>
      )}
    </div>
  );
}

export function AnomalyScreen() {
  const select = useUI((s) => s.select);
  const setTool = useUI((s) => s.setTool);
  const baseQ = useQuery({
    queryKey: ["zone-baselines"],
    queryFn: ({ signal }) => zoneBaselines(signal),
    refetchInterval: 5000,
  });
  const anomQ = useQuery({
    queryKey: ["anomaly-incidents"],
    queryFn: ({ signal }) => core.incidents(undefined, signal),
    refetchInterval: 8000,
  });

  const zones = baseQ.data?.zones ?? [];
  const anomalies: Incident[] = (anomQ.data ?? []).filter(
    (i) => i.signature === "Anomalous Activity",
  );

  return (
    <div className="flex h-full overflow-hidden">
      {/* per-zone learned baseline vs live */}
      <div className="flex-1 overflow-auto p-3">
        <div className="mb-2">
          <div className="text-[13px] font-semibold text-fg">Behavioral Baselines</div>
          <div className="text-[11px] text-fg-muted">
            Learned normal occupancy per zone vs live. Deviation beyond 3σ fires an anomaly (no signature authored).
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {baseQ.isLoading && <div className="text-[12px] text-fg-muted">loading baselines...</div>}
          {zones.map((b) => (
            <BaselineRow key={b.zone_id} b={b} />
          ))}
        </div>
      </div>

      {/* recent anomaly incidents */}
      <div className="w-[300px] shrink-0 overflow-auto border-l border-line">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">Anomaly Incidents</div>
          <div className="text-[11px] text-fg-muted">Fired from learned-baseline deviation.</div>
        </div>
        {anomalies.map((i) => (
          <button
            key={i.id}
            onClick={() => {
              select({ kind: "incident", id: i.id, label: i.title });
              setTool("reconstruction");
            }}
            className="block w-full border-b border-line/40 px-3 py-2 text-left hover:bg-panel"
          >
            <div className="flex items-center justify-between">
              <span className="text-[12px] text-red">{i.camera ?? "?"}</span>
              <span className="mono text-[10px] text-fg-muted">{hhmmss(i.created_at)}</span>
            </div>
            <div className="mt-0.5 text-[11px] text-fg-secondary">{i.title}</div>
            {i.risk_score != null && (
              <div className="mono mt-0.5 text-[9px] text-fg-muted">risk {i.risk_score}</div>
            )}
          </button>
        ))}
        {anomalies.length === 0 && (
          <div className="mono px-3 py-2 text-[11px] text-fg-muted">no anomalies in the recent window</div>
        )}
      </div>
    </div>
  );
}
