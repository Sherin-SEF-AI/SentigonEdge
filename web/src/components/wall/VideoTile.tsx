"use client";

import { Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { usePerceptionSocket } from "@/hooks/usePerceptionSocket";
import { PERCEPTION_URL, type Stream } from "@/lib/api";
import { playWhep, type WhepSession } from "@/lib/whep";
import { useUI } from "@/store/ui";

import { Overlay } from "./Overlay";

const STATUS_TONE: Record<string, string> = {
  online: "bg-green",
  connecting: "bg-amber",
  offline: "bg-red",
};

export function VideoTile({ stream }: { stream: Stream }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const sessionRef = useRef<WhepSession | null>(null);
  const [rtc, setRtc] = useState<RTCPeerConnectionState>("new");
  const [muted, setMuted] = useState(true);
  const select = useUI((s) => s.select);
  const selectedId = useUI((s) => s.selection.id);
  const selected = selectedId === stream.camera_id;
  const perc = usePerceptionSocket(stream.camera_id, PERCEPTION_URL);

  useEffect(() => {
    let cancelled = false;
    const video = videoRef.current;
    if (!video) return;
    (async () => {
      try {
        const session = await playWhep(stream.whep_url, video, (s) => !cancelled && setRtc(s));
        if (cancelled) {
          await session.close();
          return;
        }
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

  const h = stream.health;
  const tone = STATUS_TONE[h.status] ?? "bg-fg-muted";
  const connecting = rtc !== "connected";

  return (
    <button
      onClick={() =>
        select({
          kind: "camera",
          id: stream.camera_id,
          label: stream.name,
          data: {
            status: h.status,
            fps: h.fps,
            resolution: h.resolution ?? "-",
            jitter_ms: h.jitter_ms,
            reconnects: h.reconnects,
            decode_errors: h.decode_errors,
            rtc,
            whep: stream.whep_url,
          },
        })
      }
      className={`group relative block aspect-video min-h-0 w-full overflow-hidden bg-black text-left ${
        selected ? "ring-1 ring-cyan" : "ring-1 ring-line"
      }`}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted={muted}
        className="h-full w-full object-cover"
      />

      <Overlay state={perc} />

      {connecting && (
        <div className="absolute inset-0 flex items-center justify-center bg-base/70">
          <span className="mono text-[11px] uppercase tracking-widest text-fg-muted">
            {rtc === "failed" ? "no signal" : "connecting"}
          </span>
        </div>
      )}

      {/* top overlay: name + status */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between bg-gradient-to-b from-black/70 to-transparent px-2 py-1.5">
        <span className="flex items-center gap-1.5">
          <span className={`inline-block h-2 w-2 rounded-full ${tone}`} />
          <span className="text-[12px] font-medium text-fg drop-shadow">{stream.name}</span>
        </span>
        <span className="mono text-[10px] uppercase text-fg-secondary">{h.status}</span>
      </div>

      {/* bottom overlay: telemetry */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center gap-3 bg-gradient-to-t from-black/75 to-transparent px-2 py-1.5">
        <span className="mono text-[11px] text-cyan">{h.fps.toFixed(1)} fps</span>
        <span className="mono text-[11px] text-fg-secondary">{h.resolution ?? "-"}</span>
        {perc.connected && (
          <span className="mono text-[11px] text-green">{perc.objects.length} obj</span>
        )}
        <span
          role="button"
          tabIndex={-1}
          onClick={(e) => {
            e.stopPropagation();
            setMuted((m) => !m);
          }}
          className="pointer-events-auto ml-auto text-fg-muted hover:text-fg"
        >
          {muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
        </span>
      </div>
    </button>
  );
}
