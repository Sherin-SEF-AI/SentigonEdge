"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { core, ingestSnapshot } from "@/lib/api";
import { cn } from "@/lib/cn";

const ZONE_TYPES = ["restricted", "exclusion", "perimeter", "entry", "parking", "loading_dock", "production_floor", "general"];

export function ZoneEditor() {
  const qc = useQueryClient();
  const { data: cameras } = useQuery({ queryKey: ["api-cameras"], queryFn: ({ signal }) => core.cameras(signal) });
  const [cameraId, setCameraId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const [points, setPoints] = useState<number[][]>([]);
  const [name, setName] = useState("");
  const [zoneType, setZoneType] = useState("restricted");
  const [msg, setMsg] = useState("");
  const imgWrapRef = useRef<HTMLDivElement>(null);

  const active = cameraId ?? cameras?.[0]?.id ?? null;
  const { data: zones } = useQuery({
    queryKey: ["zones", active],
    queryFn: ({ signal }) => core.zones(active as string, signal),
    enabled: !!active,
  });

  useEffect(() => {
    if (!active) return;
    setSnapshot(null);
    setPoints([]);
    ingestSnapshot(active)
      .then((r) => setSnapshot(r.url))
      .catch(() => setSnapshot(null));
  }, [active]);

  function addPoint(e: React.MouseEvent) {
    const wrap = imgWrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const nx = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const ny = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    setPoints((p) => [...p, [Number(nx.toFixed(4)), Number(ny.toFixed(4))]]);
  }

  async function save() {
    if (!active || points.length < 3 || !name) return;
    setMsg("");
    try {
      await core.createZone({ name, zone_type: zoneType, camera_id: active, polygon: points });
      setMsg("zone saved");
      setPoints([]);
      setName("");
      qc.invalidateQueries({ queryKey: ["zones"] });
    } catch (e) {
      setMsg(String(e));
    }
  }

  const poly = (pts: number[][]) => pts.map((p) => `${p[0] * 100}% ${p[1] * 100}%`).join(", ");

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">ROI / Zone Editor</span>
        <select
          value={active ?? ""}
          onChange={(e) => setCameraId(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-0.5 text-[12px] text-fg outline-none"
        >
          {(cameras ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="zone name"
          className="w-40 rounded-[3px] border border-line bg-base px-2 py-0.5 text-[12px] text-fg outline-none placeholder:text-fg-muted"
        />
        <select
          value={zoneType}
          onChange={(e) => setZoneType(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-0.5 text-[12px] text-fg outline-none"
        >
          {ZONE_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <button onClick={() => setPoints([])} className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg">
          clear
        </button>
        <button
          onClick={save}
          disabled={points.length < 3 || !name}
          className="mono rounded-[2px] bg-raised px-2.5 py-0.5 text-[11px] text-cyan hover:text-fg disabled:opacity-40"
        >
          save zone ({points.length} pts)
        </button>
        {msg && <span className="mono text-[11px] text-green">{msg}</span>}
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 items-center justify-center overflow-hidden bg-black p-3">
          <div
            ref={imgWrapRef}
            onClick={addPoint}
            className="relative max-h-full cursor-crosshair select-none"
            style={{ aspectRatio: "16 / 9", width: "min(100%, 1100px)" }}
          >
            {snapshot ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={snapshot} alt="camera" className="h-full w-full object-cover" draggable={false} />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-[12px] text-fg-muted">
                capturing snapshot...
              </div>
            )}
            {/* existing zones */}
            {(zones ?? []).map((z) =>
              z.polygon && z.polygon.length >= 3 ? (
                <div
                  key={z.id}
                  className="pointer-events-none absolute inset-0"
                  style={{ clipPath: `polygon(${poly(z.polygon)})`, background: "rgba(59,167,196,0.10)", outline: "1px dashed rgba(59,167,196,0.5)" }}
                />
              ) : null,
            )}
            {/* in-progress polygon */}
            {points.length >= 3 && (
              <div
                className="pointer-events-none absolute inset-0"
                style={{ clipPath: `polygon(${poly(points)})`, background: "rgba(242,169,59,0.18)", outline: "1px solid #F2A93B" }}
              />
            )}
            {points.map((p, i) => (
              <span
                key={i}
                className="pointer-events-none absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-amber"
                style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%` }}
              />
            ))}
          </div>
        </div>

        <div className="w-56 shrink-0 overflow-auto border-l border-line bg-panel">
          <div className="border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
            Zones on this camera
          </div>
          {(zones ?? []).length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-fg-muted">none. draw one on the snapshot.</div>
          ) : (
            (zones ?? []).map((z) => (
              <div key={z.id} className="border-b border-line-soft px-3 py-1.5">
                <div className="text-[12px] text-fg">{z.name}</div>
                <div className="mono text-[10px] text-fg-muted">
                  {z.zone_type} / {z.polygon?.length ?? 0} pts
                </div>
              </div>
            ))
          )}
          <div className="px-3 py-2 text-[11px] leading-snug text-fg-muted">
            Click on the snapshot to add polygon points (3+). Save to create a real zone; the
            context engine picks it up and trips signatures on objects entering it.
          </div>
        </div>
      </div>
    </div>
  );
}
