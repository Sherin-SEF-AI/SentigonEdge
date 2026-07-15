"use client";

import { useEffect, useRef, useState } from "react";

export interface PObject {
  track_id: number;
  object_class: string;
  confidence: number;
  bbox: number[]; // [x, y, w, h] in frame pixels
  zone_hits: string[];
  attributes: Record<string, unknown>;
}

export interface PZone {
  id: string;
  name: string;
  type: string;
  poly: number[][]; // normalized 0..1
}

export interface PerceptionState {
  frameW: number;
  frameH: number;
  objects: PObject[];
  zones: PZone[];
  seq: number;
  connected: boolean;
}

const INITIAL: PerceptionState = {
  frameW: 1280,
  frameH: 720,
  objects: [],
  zones: [],
  seq: -1,
  connected: false,
};

// Subscribe to the perception overlay feed for one camera. Auto-reconnects.
export function usePerceptionSocket(cameraId: string, base: string): PerceptionState {
  const [state, setState] = useState<PerceptionState>(INITIAL);
  const zonesRef = useRef<PZone[]>([]);

  useEffect(() => {
    const url = base.replace(/^http/, "ws") + `/ws/objects/${cameraId}`;
    let ws: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      ws = new WebSocket(url);
      ws.onopen = () => setState((s) => ({ ...s, connected: true }));
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "zones") {
          zonesRef.current = m.zones ?? [];
        } else if (m.type === "objects") {
          setState({
            frameW: m.frame_width,
            frameH: m.frame_height,
            objects: m.objects ?? [],
            zones: zonesRef.current,
            seq: m.seq,
            connected: true,
          });
        }
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      ws?.close();
    };
  }, [cameraId, base]);

  return state;
}
