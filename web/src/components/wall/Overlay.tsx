"use client";

import { useEffect, useRef } from "react";

import type { PerceptionState } from "@/hooks/usePerceptionSocket";

const CLASS_COLOR: Record<string, string> = {
  person: "#3BA7C4",
  car: "#48C08A",
  truck: "#48C08A",
  bus: "#48C08A",
  motorcycle: "#48C08A",
  bicycle: "#48C08A",
  knife: "#E5484D",
};

// Draws detection boxes, track IDs, and zone polygons over the video, client-side
// from the perception bus. Frame coords are scaled to the tile's display size.
export function Overlay({ state }: { state: PerceptionState }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    const parent = canvas?.parentElement;
    if (!canvas || !parent) return;
    const W = parent.clientWidth;
    const H = parent.clientHeight;
    if (W === 0 || H === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    // zones (normalized -> tile pixels)
    for (const z of state.zones) {
      if (!z.poly?.length) continue;
      ctx.beginPath();
      z.poly.forEach((p, i) => {
        const x = p[0] * W;
        const y = p[1] * H;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
      ctx.fillStyle = "rgba(59,167,196,0.07)";
      ctx.fill();
      ctx.strokeStyle = "rgba(59,167,196,0.45)";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // detection boxes (frame coords -> tile pixels)
    const sx = W / state.frameW;
    const sy = H / state.frameH;
    ctx.font = "600 10px ui-monospace, monospace";
    for (const o of state.objects) {
      const [x, y, w, h] = o.bbox;
      const weapon = Boolean(o.attributes?.weapon);
      const color = weapon ? "#E5484D" : (CLASS_COLOR[o.object_class] ?? "#9AA0A8");
      const rx = x * sx;
      const ry = y * sy;
      const rw = w * sx;
      const rh = h * sy;
      ctx.strokeStyle = color;
      ctx.lineWidth = o.zone_hits.length ? 2 : 1.25;
      ctx.strokeRect(rx, ry, rw, rh);

      const label = `${o.object_class} ${o.track_id >= 0 ? "#" + o.track_id : ""}`.trim();
      const tw = ctx.measureText(label).width + 6;
      ctx.fillStyle = color;
      ctx.fillRect(rx, ry - 12, tw, 12);
      ctx.fillStyle = "#0E0F11";
      ctx.fillText(label, rx + 3, ry - 3);

      if (o.zone_hits.length) {
        ctx.fillStyle = "#F2A93B";
        ctx.beginPath();
        ctx.arc(rx + rw - 4, ry + 4, 3, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }, [state]);

  return <canvas ref={ref} className="pointer-events-none absolute inset-0 h-full w-full" />;
}
