"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { core, ingestSnapshot, mediasource, type UsbDevice } from "@/lib/api";
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

  // USB / v4l2 device scan + onboard (on-demand: user clicks "scan")
  const usb = useQuery({
    queryKey: ["usb-scan"],
    queryFn: ({ signal }) => mediasource.scanUsb(signal),
    enabled: false,
  });
  const [usbNames, setUsbNames] = useState<Record<string, string>>({});
  const addUsb = useMutation({
    mutationFn: (d: UsbDevice) =>
      mediasource.addUsb({
        device: d.device,
        name: usbNames[d.device]?.trim() || d.name,
        resolution: d.suggested.resolution,
        input_format: d.suggested.format,
      }),
    onSuccess: () => {
      usb.refetch();
      qc.invalidateQueries({ queryKey: ["api-cameras"] });
    },
  });

  const camName = cameras?.find((c) => c.id === active)?.name ?? "";
  const renameCam = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => core.renameCamera(id, name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-cameras"] }),
  });
  const deleteCam = useMutation({
    mutationFn: (id: string) => core.deleteCamera(id),
    onSuccess: () => {
      setCameraId(null);
      qc.invalidateQueries({ queryKey: ["api-cameras"] });
    },
  });

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
        {active && (
          <>
            <button
              onClick={() => {
                const n = window.prompt("Rename camera:", camName);
                if (n && n.trim() && n.trim() !== camName) renameCam.mutate({ id: active, name: n.trim() });
              }}
              className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg"
            >
              rename
            </button>
            <button
              onClick={() => {
                if (
                  window.confirm(
                    `Delete camera "${camName}" and ALL its data (incidents, recordings, footage)? This cannot be undone.`,
                  )
                )
                  deleteCam.mutate(active);
              }}
              disabled={deleteCam.isPending}
              className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[11px] text-red hover:text-fg disabled:opacity-40"
            >
              {deleteCam.isPending ? "deleting…" : "delete"}
            </button>
          </>
        )}
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

        <div className="w-72 shrink-0 overflow-auto border-l border-line bg-panel">
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

          {/* USB / v4l2 device cameras: scan + one-click onboard */}
          <div className="flex items-center justify-between border-y border-line px-3 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-fg-muted">USB / Device Cameras</span>
            <button
              onClick={() => usb.refetch()}
              disabled={usb.isFetching}
              className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[10px] text-cyan hover:text-fg disabled:opacity-40"
            >
              {usb.isFetching ? "scanning…" : usb.data ? "rescan" : "scan"}
            </button>
          </div>
          {usb.data === undefined ? (
            <div className="px-3 py-2 text-[11px] text-fg-muted">
              Click “scan” to detect plugged-in USB / v4l2 cameras.
            </div>
          ) : usb.data.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-fg-muted">No /dev/video* devices found.</div>
          ) : (
            usb.data.map((d) => (
              <div key={d.device} className="border-b border-line-soft px-3 py-1.5">
                <div className="truncate text-[12px] text-fg" title={d.name}>
                  {d.name}
                </div>
                <div className="mono text-[10px] text-fg-muted">
                  {d.device} · {d.capture ? `${d.suggested.format || "auto"} ${d.suggested.resolution}` : "no capture modes"}
                </div>
                {d.registered ? (
                  <div className="mono mt-1 text-[10px] text-green">✓ added</div>
                ) : d.capture ? (
                  <div className="mt-1 flex items-center gap-1">
                    <input
                      value={usbNames[d.device] ?? ""}
                      onChange={(e) => setUsbNames((m) => ({ ...m, [d.device]: e.target.value }))}
                      placeholder={d.name}
                      className="min-w-0 flex-1 rounded-[3px] border border-line bg-base px-1.5 py-0.5 text-[11px] text-fg outline-none placeholder:text-fg-muted"
                    />
                    <button
                      onClick={() => addUsb.mutate(d)}
                      disabled={addUsb.isPending}
                      className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[10px] text-cyan hover:text-fg disabled:opacity-40"
                    >
                      add
                    </button>
                  </div>
                ) : null}
              </div>
            ))
          )}
          {addUsb.isError && (
            <div className="mono px-3 py-1 text-[10px] text-red">{String(addUsb.error)}</div>
          )}
          {addUsb.isSuccess && (
            <div className="mono px-3 py-1 text-[10px] text-green">camera added — appears on the wall once frames flow.</div>
          )}
        </div>
      </div>
    </div>
  );
}
