"use client";

import { useQuery } from "@tanstack/react-query";

import { core, type PerceptionCam, type ServiceHealth } from "@/lib/api";

function num(v: unknown, d = 1): string {
  return typeof v === "number" ? v.toFixed(d) : "-";
}

function ServiceCard({ s }: { s: ServiceHealth }) {
  const st = s.stats ?? {};
  const rows: [string, string][] = [];
  if (s.name === "reason") {
    rows.push(["backend", String(st.backend ?? "-")]);
    rows.push(["confirmed", String(st.confirmed ?? "-")]);
    rows.push(["rejected", String(st.rejected ?? "-")]);
    rows.push(["avg latency", `${num(st.avg_latency_ms, 0)} ms`]);
  } else if (s.name === "notify") {
    rows.push(["sent", String(st.sent ?? "-")]);
    rows.push(["failed", String(st.failed ?? "-")]);
  } else if (s.name === "search") {
    rows.push(["indexed", String(st.indexed ?? st.points ?? "-")]);
  } else if (s.name === "ingest") {
    rows.push(["cameras", `${st.online ?? "-"}/${st.cameras ?? "-"}`]);
    rows.push(["agg fps", num(st.aggregate_fps)]);
  }
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-fg">{s.name}</span>
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${s.up ? "bg-green" : "bg-red"}`} />
      </div>
      <div className="mono mt-1 text-[10px] uppercase text-fg-muted">{s.up ? "up" : "down"}</div>
      {rows.length > 0 && (
        <div className="mt-2 space-y-0.5 border-t border-line pt-2">
          {rows.map(([k, v]) => (
            <div key={k} className="flex justify-between text-[11px]">
              <span className="text-fg-muted">{k}</span>
              <span className="mono text-fg">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CamRow({ c }: { c: PerceptionCam }) {
  const tone = c.status === "online" ? "text-green" : c.status === "offline" ? "text-red" : "text-amber";
  return (
    <tr className="border-b border-line/50">
      <td className="py-1.5 text-[12px] text-fg">{c.name}</td>
      <td className={`py-1.5 mono text-[11px] uppercase ${tone}`}>{c.status}</td>
      <td className="py-1.5 mono text-right text-[12px] text-cyan">{num(c.fps)}</td>
      <td className="py-1.5 mono text-right text-[12px] text-fg">{c.objects}</td>
      <td className="py-1.5 mono text-right text-[12px] text-fg-secondary">{num(c.inference_ms)} ms</td>
    </tr>
  );
}

export function HealthScreen() {
  const q = useQuery({
    queryKey: ["health-services"],
    queryFn: ({ signal }) => core.healthServices(signal),
    refetchInterval: 3000,
  });
  const services = q.data?.services ?? [];
  const cams = q.data?.cameras ?? [];
  const up = services.filter((s) => s.up).length;

  return (
    <div className="flex h-full flex-col overflow-auto p-4">
      <div className="mb-3">
        <div className="text-[13px] font-semibold text-fg">System Health</div>
        <div className="text-[11px] text-fg-muted">
          {up}/{services.length} services up. Live probe every 3s.
        </div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        {services.map((s) => (
          <ServiceCard key={s.name} s={s} />
        ))}
      </div>

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
        Perception workers (per camera)
      </div>
      <table className="w-full">
        <thead>
          <tr className="border-b border-line text-left text-[10px] uppercase text-fg-muted">
            <th className="py-1 font-normal">camera</th>
            <th className="py-1 font-normal">status</th>
            <th className="py-1 text-right font-normal">fps</th>
            <th className="py-1 text-right font-normal">objects</th>
            <th className="py-1 text-right font-normal">inference</th>
          </tr>
        </thead>
        <tbody>
          {cams.map((c) => (
            <CamRow key={c.camera_id} c={c} />
          ))}
          {cams.length === 0 && (
            <tr>
              <td colSpan={5} className="mono py-4 text-center text-[12px] text-fg-muted">
                perception not reporting
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
