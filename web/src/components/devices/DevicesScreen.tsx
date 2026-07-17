"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { API_URL, core, devices, type Device } from "@/lib/api";
import { cn } from "@/lib/cn";

const DEVICE_CLASSES = [
  "door_contact",
  "motion_pir",
  "environmental",
  "access_controller",
  "glassbreak",
  "panic_button",
  "generic",
];
const PROTOCOLS = ["webhook", "mqtt", "http"];

function statusColor(s: string): string {
  if (s === "online") return "text-green";
  if (s === "offline") return "text-red";
  return "text-fg-muted";
}

export function DevicesScreen() {
  const qc = useQueryClient();
  const { data: deviceList } = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => devices.list(signal),
    refetchInterval: 5000,
  });
  const { data: cameras } = useQuery({
    queryKey: ["api-cameras"],
    queryFn: ({ signal }) => core.cameras(signal),
  });

  const [selId, setSelId] = useState<string | null>(null);
  const selected = deviceList?.find((d) => d.id === selId) ?? null;

  const { data: events } = useQuery({
    queryKey: ["device-events", selId],
    queryFn: ({ signal }) => devices.events(selId as string, signal),
    enabled: !!selId,
    refetchInterval: 4000,
  });

  // add-device form state
  const [name, setName] = useState("");
  const [deviceClass, setDeviceClass] = useState("door_contact");
  const [protocol, setProtocol] = useState("webhook");
  const [externalId, setExternalId] = useState("");
  const [vendor, setVendor] = useState("");
  const [cameraId, setCameraId] = useState("");

  const addDevice = useMutation({
    mutationFn: () =>
      devices.create({
        name: name.trim(),
        device_class: deviceClass,
        protocol,
        external_id: externalId.trim() || null,
        vendor: vendor.trim() || null,
        camera_id: cameraId || null,
      }),
    onSuccess: (d) => {
      setName("");
      setExternalId("");
      setVendor("");
      setCameraId("");
      setSelId(d.id);
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
  });

  const delDevice = useMutation({
    mutationFn: (id: string) => devices.remove(id),
    onSuccess: () => {
      setSelId(null);
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
  });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
          Devices / Sensors
        </span>
        <span className="mono text-[11px] text-fg-muted">
          {deviceList?.length ?? 0} registered · signal-plane (non-camera)
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* left: device list + add form */}
        <div className="flex w-[380px] shrink-0 flex-col overflow-auto border-r border-line bg-panel">
          <div className="border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
            Registered Devices
          </div>
          {(deviceList ?? []).length === 0 ? (
            <div className="px-3 py-2 text-[12px] text-fg-muted">
              No devices yet. Register one below — door contacts, motion (PIR), environmental
              sensors, access controllers, or any generic webhook/MQTT feed.
            </div>
          ) : (
            (deviceList ?? []).map((d: Device) => (
              <button
                key={d.id}
                onClick={() => setSelId(d.id)}
                className={cn(
                  "flex flex-col items-start gap-0.5 border-b border-line-soft px-3 py-1.5 text-left hover:bg-raised",
                  selId === d.id && "bg-raised",
                )}
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="truncate text-[12px] text-fg">{d.name}</span>
                  <span className={cn("mono text-[10px]", statusColor(d.status))}>{d.status}</span>
                </div>
                <div className="mono text-[10px] text-fg-muted">
                  {d.device_class} · {d.protocol}
                  {d.external_id ? ` · ${d.external_id}` : ""}
                </div>
              </button>
            ))
          )}

          {/* add device */}
          <div className="mt-2 border-y border-line px-3 py-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
            Register Device
          </div>
          <div className="space-y-1.5 px-3 py-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="name (e.g. Loading Dock Door)"
              className="w-full rounded-[3px] border border-line bg-base px-1.5 py-1 text-[12px] text-fg outline-none placeholder:text-fg-muted"
            />
            <div className="flex gap-1.5">
              <select
                value={deviceClass}
                onChange={(e) => setDeviceClass(e.target.value)}
                className="min-w-0 flex-1 rounded-[3px] border border-line bg-base px-1.5 py-1 text-[11px] text-fg outline-none"
              >
                {DEVICE_CLASSES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <select
                value={protocol}
                onChange={(e) => setProtocol(e.target.value)}
                className="w-24 rounded-[3px] border border-line bg-base px-1.5 py-1 text-[11px] text-fg outline-none"
              >
                {PROTOCOLS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <input
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              placeholder="external id (routing key, e.g. door-dock-01)"
              className="mono w-full rounded-[3px] border border-line bg-base px-1.5 py-1 text-[11px] text-fg outline-none placeholder:text-fg-muted"
            />
            <input
              value={vendor}
              onChange={(e) => setVendor(e.target.value)}
              placeholder="vendor (optional)"
              className="w-full rounded-[3px] border border-line bg-base px-1.5 py-1 text-[11px] text-fg outline-none placeholder:text-fg-muted"
            />
            <select
              value={cameraId}
              onChange={(e) => setCameraId(e.target.value)}
              className="w-full rounded-[3px] border border-line bg-base px-1.5 py-1 text-[11px] text-fg outline-none"
            >
              <option value="">co-located camera (optional, for fusion)</option>
              {(cameras ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <button
              onClick={() => addDevice.mutate()}
              disabled={!name.trim() || addDevice.isPending}
              className="mono w-full rounded-[2px] bg-raised px-2 py-1 text-[11px] text-cyan hover:text-fg disabled:opacity-40"
            >
              {addDevice.isPending ? "registering…" : "register device"}
            </button>
            {addDevice.isError && (
              <div className="mono text-[10px] text-red">{String(addDevice.error)}</div>
            )}
          </div>
        </div>

        {/* right: selected device detail + recent events */}
        <div className="flex min-w-0 flex-1 flex-col overflow-auto">
          {!selected ? (
            <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">
              Select a device to see its detail and recent events.
            </div>
          ) : (
            <div className="flex flex-col gap-3 p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-[15px] font-semibold text-fg">{selected.name}</div>
                  <div className="mono text-[11px] text-fg-muted">
                    {selected.device_class} · {selected.protocol} ·{" "}
                    <span className={statusColor(selected.status)}>{selected.status}</span>
                  </div>
                </div>
                <button
                  onClick={() => {
                    if (window.confirm(`Delete device "${selected.name}" and all its events?`))
                      delDevice.mutate(selected.id);
                  }}
                  disabled={delDevice.isPending}
                  className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[11px] text-red hover:text-fg disabled:opacity-40"
                >
                  {delDevice.isPending ? "deleting…" : "delete"}
                </button>
              </div>

              <div className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-[4px] border border-line bg-panel p-3 text-[11px]">
                <Row k="external id" v={selected.external_id ?? "—"} mono />
                <Row k="vendor" v={selected.vendor ?? "—"} />
                <Row k="camera bound" v={selected.camera_id ? "yes" : "—"} />
                <Row
                  k="last seen"
                  v={selected.last_seen ? new Date(selected.last_seen).toLocaleString() : "never"}
                />
              </div>

              {/* how to send events to this device */}
              <div className="rounded-[4px] border border-line bg-panel p-3">
                <div className="text-[10px] uppercase tracking-wide text-fg-muted">
                  Send events to this device
                </div>
                <div className="mono mt-1 break-all text-[11px] text-cyan">
                  POST {API_URL}/sensor-events
                </div>
                <pre className="mono mt-1 overflow-x-auto rounded-[3px] bg-base p-2 text-[10px] text-fg-secondary">
{`{ "external_id": ${JSON.stringify(selected.external_id ?? "<set an external id>")},
  "event_type": "state_change",
  "state": "open" }`}
                </pre>
                <div className="mt-1 text-[10px] text-fg-muted">
                  A matching event trips any sensor signature and can fuse with camera detections
                  on the bound camera.
                </div>
              </div>

              {/* recent events */}
              <div className="rounded-[4px] border border-line bg-panel">
                <div className="border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
                  Recent Events
                </div>
                {(events ?? []).length === 0 ? (
                  <div className="px-3 py-2 text-[12px] text-fg-muted">No events received yet.</div>
                ) : (
                  <table className="w-full text-[11px]">
                    <thead className="text-fg-muted">
                      <tr className="border-b border-line-soft">
                        <th className="px-3 py-1 text-left font-normal">time</th>
                        <th className="px-3 py-1 text-left font-normal">type</th>
                        <th className="px-3 py-1 text-left font-normal">state / value</th>
                        <th className="px-3 py-1 text-left font-normal">sev</th>
                      </tr>
                    </thead>
                    <tbody className="mono">
                      {(events ?? []).map((e) => (
                        <tr key={e.id} className="border-b border-line-soft">
                          <td className="px-3 py-1 text-fg-muted">
                            {new Date(e.ts).toLocaleTimeString()}
                          </td>
                          <td className="px-3 py-1 text-fg">{e.event_type}</td>
                          <td className="px-3 py-1 text-fg-secondary">
                            {e.state ?? (e.value != null ? `${e.value}${e.unit ?? ""}` : "—")}
                          </td>
                          <td className="px-3 py-1 text-fg-muted">{e.severity ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-fg-muted">{k}</span>
      <span className={cn("truncate text-fg", mono && "mono")}>{v}</span>
    </div>
  );
}
