"use client";

import { useQuery } from "@tanstack/react-query";

import { core } from "@/lib/api";

const SEV_COLOR: Record<string, string> = {
  critical: "text-red",
  high: "text-amber",
  medium: "text-cyan",
  low: "text-green",
};

function Stat({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="border border-line bg-panel p-3">
      <div className="text-[10px] uppercase tracking-wide text-fg-muted">{label}</div>
      <div className={`mono mt-1 text-[22px] ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

export function HandoverScreen() {
  const q = useQuery({
    queryKey: ["shift-handover"],
    queryFn: ({ signal }) => core.shiftHandover(8, signal),
    refetchInterval: 10000,
  });
  const d = q.data;

  return (
    <div className="flex h-full flex-col overflow-auto p-4">
      <div className="mb-3">
        <div className="text-[13px] font-semibold text-fg">Shift Handover</div>
        <div className="text-[11px] text-fg-muted">
          What the next operator inherits. Last {d?.shift_hours ?? 8}h.
        </div>
      </div>

      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Incidents this shift" value={d.incidents_this_shift} />
            <Stat label="Unacknowledged" value={d.unacknowledged} tone="text-amber" />
            <Stat label="Escalations" value={d.escalations_this_shift} tone={d.escalations_this_shift > 0 ? "text-red" : "text-fg"} />
            <Stat
              label="Cameras online"
              value={`${d.cameras.filter((c) => c.status === "online").length}/${d.cameras.length}`}
              tone="text-green"
            />
          </div>

          <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Open by severity</div>
              <div className="space-y-1">
                {Object.entries(d.open_by_severity).sort().map(([sev, n]) => (
                  <div key={sev} className="flex items-center justify-between border border-line bg-panel px-3 py-1.5">
                    <span className={`text-[12px] uppercase ${SEV_COLOR[sev] ?? "text-fg"}`}>{sev}</span>
                    <span className="mono text-[13px] text-fg">{n}</span>
                  </div>
                ))}
                {Object.keys(d.open_by_severity).length === 0 && (
                  <div className="mono text-[12px] text-green">no open incidents</div>
                )}
              </div>
            </div>
            <div>
              <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Camera health</div>
              <div className="space-y-1">
                {d.cameras.map((c) => (
                  <div key={c.name} className="flex items-center justify-between border border-line bg-panel px-3 py-1.5">
                    <span className="text-[12px] text-fg">{c.name}</span>
                    <span className={`mono text-[11px] uppercase ${c.status === "online" ? "text-green" : "text-red"}`}>
                      {c.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
            Top open incidents to hand over ({d.top_open_incidents.length})
          </div>
          <div className="space-y-1">
            {d.top_open_incidents.map((i) => (
              <div key={i.id} className="flex items-center justify-between border border-line bg-panel px-3 py-1.5">
                <span className="text-[12px] text-fg">{i.title}</span>
                <span className="flex items-center gap-3">
                  <span className={`mono text-[10px] uppercase ${SEV_COLOR[i.severity] ?? "text-fg"}`}>{i.severity}</span>
                  <span className="mono text-[10px] text-fg-muted">{i.created_at?.slice(11, 19)}</span>
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
