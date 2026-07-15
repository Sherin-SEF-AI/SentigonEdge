"use client";

import { useQuery } from "@tanstack/react-query";

import { fusionTimeline, type AccessSignal, type VideoSignal } from "@/lib/api";
import { useUI } from "@/store/ui";

function hhmmss(ts?: string | null): string {
  return ts ? ts.slice(11, 19) : "--";
}

const ACCESS_LABEL: Record<string, string> = {
  door_forced: "Door forced",
  door_held: "Door held open",
  access_denied: "Access denied",
  zone_trip: "Zone trip",
  access_granted: "Access granted",
};

function sevColor(sev: string): string {
  switch (sev.toLowerCase()) {
    case "critical":
    case "high":
      return "text-red";
    case "medium":
      return "text-amber";
    default:
      return "text-green";
  }
}

function AccessRow({ a, fused }: { a: AccessSignal; fused: boolean }) {
  const select = useUI((s) => s.select);
  const setTool = useUI((s) => s.setTool);
  const color = a.threatening ? (fused ? "text-magenta" : "text-red") : "text-green";
  const clickable = !!a.bound_incident;
  const goToIncident = () => {
    if (!a.bound_incident) return;
    select({ kind: "incident", id: a.bound_incident, label: ACCESS_LABEL[a.event_type] ?? a.event_type });
    setTool("reconstruction");
  };
  const Tag = clickable ? "button" : "div";
  return (
    <Tag
      onClick={clickable ? goToIncident : undefined}
      className={`block w-full border px-3 py-1.5 text-left ${
        fused ? "border-magenta/60 bg-magenta/10" : a.threatening ? "border-red/30 bg-panel" : "border-line bg-panel"
      } ${clickable ? "cursor-pointer hover:bg-magenta/20" : ""}`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-[12px] ${color}`}>{ACCESS_LABEL[a.event_type] ?? a.event_type}</span>
        <span className="mono text-[10px] text-fg-muted">{hhmmss(a.ts)}</span>
      </div>
      <div className="mono mt-0.5 flex flex-wrap gap-x-2 text-[10px] text-fg-muted">
        {a.door_id && <span>door {a.door_id}</span>}
        {a.badge_id && <span>badge {a.badge_id}</span>}
        {a.camera && <span>· {a.camera}</span>}
        {fused && <span className="text-magenta">FUSED → open incident</span>}
      </div>
    </Tag>
  );
}

function VideoRow({ v }: { v: VideoSignal }) {
  const select = useUI((s) => s.select);
  const setTool = useUI((s) => s.setTool);
  return (
    <button
      onClick={() => {
        select({ kind: "incident", id: v.id, label: v.signature ?? "incident" });
        setTool("reconstruction");
      }}
      className={`block w-full border px-3 py-1.5 text-left hover:bg-raised ${
        v.fused ? "border-magenta/60 bg-magenta/10" : "border-line bg-panel"
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-[12px] ${sevColor(v.severity)}`}>{v.signature}</span>
        <span className="mono text-[10px] text-fg-muted">{hhmmss(v.ts)}</span>
      </div>
      <div className="mono mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] text-fg-muted">
        {v.camera && <span>{v.camera}</span>}
        {v.risk_score != null && <span>· risk {v.risk_score}</span>}
        {v.status === "escalated" && <span className="text-red">· escalated</span>}
        {v.fused && <span className="text-magenta">· FUSED</span>}
      </div>
    </button>
  );
}

export function FusionScreen() {
  const q = useQuery({
    queryKey: ["fusion-timeline"],
    queryFn: ({ signal }) => fusionTimeline(60, signal),
    refetchInterval: 5000,
  });
  const data = q.data;
  const fusedIncidentIds = new Set((data?.fusions ?? []).map((f) => f.incident_id));

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div>
          <div className="text-[13px] font-semibold text-fg">Signals Fusion</div>
          <div className="text-[11px] text-fg-muted">
            Access-control signals and video incidents. A fused pair (magenta) is a verified, elevated threat.
          </div>
        </div>
        <div className="mono flex gap-2 text-[10px]">
          <span className="rounded-[2px] border border-line px-1.5 py-0.5 text-fg-muted">
            access {data?.counts.access ?? 0}
          </span>
          <span className="rounded-[2px] border border-line px-1.5 py-0.5 text-fg-muted">
            video {data?.counts.video ?? 0}
          </span>
          <span className="rounded-[2px] border border-magenta/50 bg-magenta/10 px-1.5 py-0.5 text-magenta">
            fused {data?.counts.fused ?? 0}
          </span>
        </div>
      </div>

      <div className="grid flex-1 grid-cols-2 gap-3 overflow-hidden p-3">
        {/* access-control lane */}
        <div className="flex flex-col overflow-hidden">
          <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
            Access Control Signals
          </div>
          <div className="flex-1 space-y-1.5 overflow-auto pr-1">
            {(data?.access_events ?? []).map((a) => (
              <AccessRow key={a.id} a={a} fused={!!a.bound_incident} />
            ))}
            {(data?.access_events ?? []).length === 0 && (
              <div className="mono text-[11px] text-fg-muted">no access-control signals in the window</div>
            )}
          </div>
        </div>

        {/* video lane */}
        <div className="flex flex-col overflow-hidden">
          <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
            Video Incidents
          </div>
          <div className="flex-1 space-y-1.5 overflow-auto pr-1">
            {(data?.video_incidents ?? []).map((v) => (
              <VideoRow key={v.id} v={{ ...v, fused: v.fused || fusedIncidentIds.has(v.id) }} />
            ))}
            {q.isLoading && <div className="mono text-[11px] text-fg-muted">loading...</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
