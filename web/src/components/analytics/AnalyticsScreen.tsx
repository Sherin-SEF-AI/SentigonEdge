"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { core } from "@/lib/api";

const SEV = { critical: "#E5484D", high: "#F2A93B", medium: "#3BA7C4", low: "#48C08A", info: "#5B616B" };
const AXIS = { fill: "#5B616B", fontSize: 10, fontFamily: "ui-monospace, monospace" };
const TOOLTIP = {
  contentStyle: { background: "#16181C", border: "1px solid #2A2E35", borderRadius: 3, fontSize: 11 },
  labelStyle: { color: "#9AA0A8" },
};

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col overflow-hidden rounded-[3px] border border-line bg-panel">
      <div className="border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
        {title}
      </div>
      <div className="min-h-0 flex-1 p-2">{children}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-[3px] border border-line bg-panel px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-fg-muted">{label}</div>
      <div className={`mono text-[22px] ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

export function AnalyticsScreen() {
  const overview = useQuery({ queryKey: ["an-overview"], queryFn: ({ signal }) => core.analyticsOverview(signal), refetchInterval: 5000 });
  const ts = useQuery({ queryKey: ["an-ts"], queryFn: ({ signal }) => core.analyticsTimeseries(signal), refetchInterval: 5000 });
  const bySig = useQuery({ queryKey: ["an-sig"], queryFn: ({ signal }) => core.analyticsBySignature(signal), refetchInterval: 8000 });
  const byCam = useQuery({ queryKey: ["an-cam"], queryFn: ({ signal }) => core.analyticsByCamera(signal), refetchInterval: 8000 });

  const o = overview.data;
  const sevData = o
    ? Object.entries(o.by_severity).map(([k, v]) => ({ name: k, value: v, fill: SEV[k as keyof typeof SEV] ?? "#5B616B" }))
    : [];

  return (
    <div className="h-full overflow-auto p-3">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
        <Stat label="total incidents" value={String(o?.total_incidents ?? "-")} />
        <Stat label="critical" value={String(o?.by_severity?.critical ?? 0)} tone="text-red" />
        <Stat label="vlm confirmed" value={String(o?.confirmed ?? 0)} tone="text-green" />
        <Stat label="vlm rejected" value={String(o?.rejected ?? 0)} tone="text-fg-secondary" />
        <Stat label="false-alarm rate" value={o ? `${(o.false_alarm_rate * 100).toFixed(0)}%` : "-"} tone="text-cyan" />
      </div>

      <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Panel title="Incidents over time (by severity)">
            <div style={{ height: 220 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={ts.data ?? []}>
                  <XAxis dataKey="t" tick={AXIS} stroke="#2A2E35" />
                  <YAxis tick={AXIS} stroke="#2A2E35" width={28} />
                  <Tooltip {...TOOLTIP} />
                  {(["critical", "high", "medium"] as const).map((s) => (
                    <Area key={s} type="monotone" dataKey={s} stackId="1" stroke={SEV[s]} fill={SEV[s]} fillOpacity={0.25} />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </div>
        <Panel title="By severity">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={sevData} dataKey="value" nameKey="name" innerRadius={45} outerRadius={80} paddingAngle={2}>
                  {sevData.map((d) => (
                    <Cell key={d.name} fill={d.fill} stroke="#0E0F11" />
                  ))}
                </Pie>
                <Tooltip {...TOOLTIP} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>

      <div className="mt-2 grid grid-cols-1 gap-2 lg:grid-cols-2">
        <Panel title="Top signatures">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={bySig.data ?? []} layout="vertical" margin={{ left: 30 }}>
                <XAxis type="number" tick={AXIS} stroke="#2A2E35" />
                <YAxis type="category" dataKey="signature" tick={AXIS} stroke="#2A2E35" width={110} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "#1D2024" }} />
                <Bar dataKey="count" fill="#3BA7C4" radius={[0, 2, 2, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
        <Panel title="Incidents by camera">
          <div style={{ height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={byCam.data ?? []}>
                <XAxis dataKey="camera" tick={AXIS} stroke="#2A2E35" />
                <YAxis tick={AXIS} stroke="#2A2E35" width={28} />
                <Tooltip {...TOOLTIP} cursor={{ fill: "#1D2024" }} />
                <Bar dataKey="count" fill="#48C08A" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>
    </div>
  );
}
