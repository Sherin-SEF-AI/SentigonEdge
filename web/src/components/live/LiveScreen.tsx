"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Overlay } from "@/components/wall/Overlay";
import { usePerceptionSocket } from "@/hooks/usePerceptionSocket";
import { core, ingest, PERCEPTION_URL, type Stream } from "@/lib/api";
import { playWhep, type WhepSession } from "@/lib/whep";

function LivePlayer({ stream }: { stream: Stream }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const sessionRef = useRef<WhepSession | null>(null);
  const [rtc, setRtc] = useState<RTCPeerConnectionState>("new");
  const perc = usePerceptionSocket(stream.camera_id, PERCEPTION_URL);

  useEffect(() => {
    let cancelled = false;
    const video = videoRef.current;
    if (!video) return;
    (async () => {
      try {
        const session = await playWhep(stream.whep_url, video, (s) => !cancelled && setRtc(s));
        if (cancelled) return void (await session.close());
        sessionRef.current = session;
      } catch {
        if (!cancelled) setRtc("failed");
      }
    })();
    return () => {
      cancelled = true;
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, [stream.whep_url]);

  return (
    <div className="relative aspect-video w-full overflow-hidden bg-black ring-1 ring-line">
      <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-contain" />
      <Overlay state={perc} />
      {rtc !== "connected" && (
        <div className="absolute inset-0 flex items-center justify-center bg-base/70">
          <span className="mono text-[11px] uppercase tracking-widest text-fg-muted">
            {rtc === "failed" ? "no signal" : "connecting"}
          </span>
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex gap-3 bg-gradient-to-t from-black/75 to-transparent px-3 py-2">
        <span className="mono text-[12px] text-cyan">{stream.health.fps.toFixed(1)} fps</span>
        <span className="mono text-[12px] text-fg-secondary">{stream.health.resolution ?? "-"}</span>
        {perc.connected && <span className="mono text-[12px] text-green">{perc.objects.length} obj</span>}
        <span className="mono ml-auto text-[12px] text-fg-muted">{stream.name}</span>
      </div>
    </div>
  );
}

export function LiveScreen() {
  const streamsQ = useQuery({ queryKey: ["streams"], queryFn: ({ signal }) => ingest.streams(signal), refetchInterval: 4000 });
  const streams = streamsQ.data ?? [];
  const [selId, setSelId] = useState<string | null>(null);
  const sel = streams.find((s) => s.camera_id === selId) ?? streams[0];

  const incQ = useQuery({
    queryKey: ["live-incidents", sel?.camera_id],
    queryFn: ({ signal }) => core.incidents(undefined, signal),
    enabled: !!sel,
    refetchInterval: 4000,
  });
  const incidents = (incQ.data ?? []).filter((i) => i.camera_id === sel?.camera_id).slice(0, 15);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="w-[220px] shrink-0 overflow-auto border-r border-line">
        <div className="border-b border-line px-3 py-2 text-[13px] font-semibold text-fg">Cameras</div>
        {streams.map((s) => {
          const active = s.camera_id === (sel?.camera_id ?? "");
          return (
            <button key={s.camera_id} onClick={() => setSelId(s.camera_id)}
              className={`flex w-full items-center justify-between px-3 py-2 text-left hover:bg-panel ${active ? "bg-panel" : ""}`}>
              <span className="text-[12px] text-fg">{s.name}</span>
              <span className={`inline-block h-2 w-2 rounded-full ${s.health.status === "online" ? "bg-green" : "bg-red"}`} />
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-auto p-4">
        {sel ? <LivePlayer stream={sel} /> : <div className="mono text-[12px] text-fg-muted">no cameras</div>}
      </div>

      <div className="w-[300px] shrink-0 overflow-auto border-l border-line p-3">
        <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
          Recent incidents · {sel?.name ?? ""}
        </div>
        <div className="space-y-1">
          {incidents.map((i) => (
            <div key={i.id} className="border border-line bg-panel px-2 py-1.5">
              <div className="flex justify-between">
                <span className="text-[12px] text-fg">{i.title}</span>
                <span className="mono text-[10px] text-fg-muted">{i.created_at.slice(11, 19)}</span>
              </div>
              <div className="mono text-[10px] uppercase text-fg-muted">
                {i.severity} · {i.status} {i.verdict ? `· ${i.verdict}` : ""}
              </div>
            </div>
          ))}
          {incidents.length === 0 && (
            <div className="mono text-[12px] text-fg-muted">no incidents on this camera yet</div>
          )}
        </div>
      </div>
    </div>
  );
}
