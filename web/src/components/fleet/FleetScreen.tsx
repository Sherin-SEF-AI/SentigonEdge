"use client";

import { useQuery } from "@tanstack/react-query";

import {
  fleet,
  type FleetCamera,
  type FleetFinding,
  type FleetOverview,
  type FleetService,
} from "@/lib/api";

const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red",
  high: "text-amber",
  medium: "text-cyan",
  low: "text-fg-muted",
  info: "text-fg-muted",
};

function timeAgo(iso?: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function pctTone(v: number | null | undefined): string {
  if (v == null) return "text-fg";
  if (v >= 95) return "text-red";
  if (v >= 85) return "text-amber";
  return "text-green";
}

function KpiTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="border border-line bg-panel px-3 py-2">
      <div className="mono text-[10px] uppercase text-fg-muted">{label}</div>
      <div className={`mono mt-0.5 text-[18px] ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v)}%`;
}

function FindingCard({ f }: { f: FleetFinding }) {
  const sev = f.severity?.toLowerCase() ?? "info";
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-center gap-2">
        <span className={`mono text-[10px] uppercase ${SEVERITY_COLOR[sev] ?? "text-fg-muted"}`}>{f.severity}</span>
        <span className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[9px] uppercase text-fg-secondary">{f.kind}</span>
        <span className="mono ml-auto text-[10px] text-fg-muted">{timeAgo(f.last_seen_at)}</span>
      </div>
      <div className="mt-1 text-[12px] text-fg">{f.target_name ?? f.target_id ?? f.target_type}</div>
      {f.detail && <div className="mt-0.5 text-[11px] text-fg-secondary">{f.detail}</div>}
      {f.recommended_action && (
        <div className="mono mt-1.5 rounded-[2px] border border-line bg-base px-1.5 py-1 text-[10px] text-cyan">
          → {f.recommended_action}
        </div>
      )}
    </div>
  );
}

function CameraCard({ c }: { c: FleetCamera }) {
  const online = c.status === "online";
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-center justify-between">
        <span className="truncate text-[12px] text-fg">{c.name}</span>
        <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${online ? "bg-green" : "bg-red"}`} />
      </div>
      <div className="mono mt-1 text-[10px] uppercase text-fg-muted">{c.status}</div>
      <div className="mt-2 space-y-0.5 border-t border-line pt-2 text-[11px]">
        <div className="flex justify-between">
          <span className="text-fg-muted">fps</span>
          <span className="mono text-cyan">
            {c.health?.fps != null ? Number(c.health.fps).toFixed(1) : "—"}
            <span className="text-fg-muted"> / {c.target_fps ?? "—"}</span>
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-fg-muted">resolution</span>
          <span className="mono text-fg-secondary">{c.health?.resolution ?? "—"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-fg-muted">last seen</span>
          <span className="mono text-fg-secondary">{timeAgo(c.last_seen)}</span>
        </div>
      </div>
    </div>
  );
}

function ServiceCard({ s }: { s: FleetService }) {
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-center justify-between">
        <span className="truncate text-[12px] text-fg">{s.name}</span>
        <span className={`inline-block h-2.5 w-2.5 shrink-0 rounded-full ${s.up ? "bg-green" : "bg-red"}`} />
      </div>
      <div className="mono mt-1 text-[10px] uppercase text-fg-muted">{s.up ? "up" : "down"}</div>
      <div className="mt-2 space-y-0.5 border-t border-line pt-2 text-[11px]">
        <div className="flex justify-between">
          <span className="text-fg-muted">latency</span>
          <span className="mono text-fg">{s.latency_ms != null ? `${Math.round(s.latency_ms)} ms` : "—"}</span>
        </div>
        {s.detail && <div className="text-[10px] text-fg-muted">{s.detail}</div>}
      </div>
    </div>
  );
}

export function FleetScreen() {
  const overviewQ = useQuery({
    queryKey: ["fleet-overview"],
    queryFn: ({ signal }) => fleet.overview(signal),
    refetchInterval: 4000,
  });
  const findingsQ = useQuery({
    queryKey: ["fleet-findings"],
    queryFn: ({ signal }) => fleet.findings(signal),
    refetchInterval: 5000,
  });
  const camerasQ = useQuery({
    queryKey: ["fleet-cameras"],
    queryFn: ({ signal }) => fleet.cameras(signal),
    refetchInterval: 5000,
  });
  const servicesQ = useQuery({
    queryKey: ["fleet-services"],
    queryFn: ({ signal }) => fleet.services(signal),
    refetchInterval: 4000,
  });

  const ov = overviewQ.data as FleetOverview | undefined;
  const host = ov?.host ?? { disk_pct: null, mem_pct: null, gpu_pct: null, load1: null };
  const findings = (findingsQ.data ?? [])
    .slice()
    .sort(
      (a, b) =>
        (SEVERITY_ORDER[a.severity?.toLowerCase()] ?? 9) - (SEVERITY_ORDER[b.severity?.toLowerCase()] ?? 9),
    );
  const cameras: FleetCamera[] = camerasQ.data ?? [];
  const services: FleetService[] = servicesQ.data ?? [];

  return (
    <div className="flex h-full flex-col overflow-auto p-4">
      <div className="mb-3">
        <div className="text-[13px] font-semibold text-fg">Fleet Health</div>
        <div className="text-[11px] text-fg-muted">Cameras, services, and host resources across the fleet.</div>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <KpiTile
          label="cameras"
          value={`${ov?.cameras_online ?? "—"}/${ov?.cameras_total ?? "—"}`}
          tone={ov && ov.cameras_online < ov.cameras_total ? "text-amber" : "text-green"}
        />
        <KpiTile
          label="services"
          value={`${ov?.services_up ?? "—"}/${ov?.services_total ?? "—"}`}
          tone={ov && ov.services_up < ov.services_total ? "text-amber" : "text-green"}
        />
        <KpiTile
          label="findings"
          value={String(ov?.findings_active ?? 0)}
          tone={(ov?.findings_active ?? 0) > 0 ? "text-amber" : "text-green"}
        />
        <KpiTile label="disk" value={pct(host.disk_pct)} tone={pctTone(host.disk_pct)} />
        <KpiTile label="mem" value={pct(host.mem_pct)} tone={pctTone(host.mem_pct)} />
        <KpiTile label="gpu" value={pct(host.gpu_pct)} tone={pctTone(host.gpu_pct)} />
        <KpiTile label="load1" value={host.load1 != null ? host.load1.toFixed(2) : "—"} />
      </div>

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Active findings</div>
      {findings.length === 0 ? (
        <div className="mono mb-6 border border-line bg-panel p-4 text-[12px] text-green">all clear — no active findings</div>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
          {findings.map((f) => (
            <FindingCard key={f.id} f={f} />
          ))}
        </div>
      )}

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Cameras</div>
      {cameras.length === 0 ? (
        <div className="mono mb-6 text-[12px] text-fg-muted">no cameras reporting</div>
      ) : (
        <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {cameras.map((c) => (
            <CameraCard key={c.id} c={c} />
          ))}
        </div>
      )}

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Services</div>
      {services.length === 0 ? (
        <div className="mono text-[12px] text-fg-muted">no services reporting</div>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {services.map((s) => (
            <ServiceCard key={s.name} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}
