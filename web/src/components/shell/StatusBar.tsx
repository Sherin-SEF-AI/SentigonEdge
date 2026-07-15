"use client";

import { useEffect, useState } from "react";

import { useCoreSummary } from "@/hooks/useCore";
import { useSummary } from "@/hooks/useIngest";

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wide text-fg-muted">{label}</span>
      <span className={`mono text-[12px] ${tone ?? "text-fg-secondary"}`}>{value}</span>
    </span>
  );
}

export function StatusBar() {
  const { data: summary } = useSummary();
  const { data: coreSummary } = useCoreSummary();
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const cams = summary?.cameras ?? 0;
  const online = summary?.online ?? 0;
  const fps = summary?.aggregate_fps ?? 0;
  const connected = summary !== undefined;

  return (
    <footer className="flex items-center gap-4 border-t border-line bg-panel px-3 text-[12px]">
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block h-2 w-2 rounded-full ${connected ? "bg-green" : "bg-red"}`}
        />
        <span className="text-[11px] font-medium text-fg-secondary">
          {connected ? "INGEST LIVE" : "INGEST DOWN"}
        </span>
      </span>
      <span className="text-line">|</span>
      <Stat label="cams" value={`${online}/${cams}`} tone={online === cams && cams > 0 ? "text-green" : "text-amber"} />
      <Stat label="fps" value={fps.toFixed(1)} />
      <Stat label="pipeline" value="--" />
      <Stat label="gpu" value="--" />
      <Stat
        label="incidents"
        value={String(coreSummary?.open_incidents ?? 0)}
        tone={(coreSummary?.open_incidents ?? 0) > 0 ? "text-red" : "text-fg-secondary"}
      />
      <Stat label="critical" value={String(coreSummary?.by_severity?.critical ?? 0)} tone="text-red" />
      <Stat label="total" value={String(coreSummary?.total_incidents ?? 0)} />
      <div className="ml-auto flex items-center gap-4">
        <Stat label="utc" value={now ? now.toISOString().slice(11, 19) : "--:--:--"} tone="text-fg" />
        <Stat
          label="local"
          value={now ? now.toLocaleTimeString([], { hour12: false }) : "--:--:--"}
        />
      </div>
    </footer>
  );
}
