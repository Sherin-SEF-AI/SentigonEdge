"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { core, type GraphNode } from "@/lib/api";

const KIND_ORDER = ["camera", "zone", "signature", "incident"] as const;
const KIND_COLOR: Record<string, string> = {
  camera: "#3BA7C4",
  zone: "#C44BC4",
  signature: "#F2A93B",
  incident: "#E5484D",
};
const SEV_COLOR: Record<string, string> = {
  critical: "#E5484D",
  high: "#F2A93B",
  medium: "#3BA7C4",
  low: "#48C08A",
};

const COL_X = { camera: 140, zone: 440, signature: 740, incident: 1040 };
const W = 1200;
const ROW_H = 34;
const PAD = 40;

export function GraphScreen() {
  const q = useQuery({ queryKey: ["graph"], queryFn: ({ signal }) => core.graph(signal), refetchInterval: 5000 });
  const [hover, setHover] = useState<string | null>(null);

  const layout = useMemo(() => {
    const nodes = q.data?.nodes ?? [];
    const edges = q.data?.edges ?? [];
    const byKind: Record<string, GraphNode[]> = { camera: [], zone: [], signature: [], incident: [] };
    for (const n of nodes) (byKind[n.kind] ??= []).push(n);
    const pos: Record<string, { x: number; y: number }> = {};
    for (const kind of KIND_ORDER) {
      const col = byKind[kind] ?? [];
      col.forEach((n, i) => {
        pos[n.id] = { x: COL_X[kind], y: PAD + i * ROW_H };
      });
    }
    const maxRows = Math.max(1, ...KIND_ORDER.map((k) => (byKind[k] ?? []).length));
    const height = PAD * 2 + maxRows * ROW_H;
    const adj: Record<string, Set<string>> = {};
    for (const e of edges) {
      (adj[e.source] ??= new Set()).add(e.target);
      (adj[e.target] ??= new Set()).add(e.source);
    }
    return { nodes, edges, pos, height, adj, byKind };
  }, [q.data]);

  const lit = (id: string) =>
    !hover || hover === id || layout.adj[hover]?.has(id) || layout.adj[id]?.has(hover);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <div>
          <div className="text-[13px] font-semibold text-fg">Context Graph</div>
          <div className="text-[11px] text-fg-muted">
            Cameras, zones, signatures and recent incidents, linked by what fired where.
          </div>
        </div>
        <div className="flex gap-3 text-[10px] uppercase text-fg-muted">
          {KIND_ORDER.map((k) => (
            <span key={k} className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: KIND_COLOR[k] }} />
              {k}
            </span>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-auto">
        <svg width={W} height={layout.height} className="min-w-full">
          {layout.edges.map((e, i) => {
            const a = layout.pos[e.source];
            const b = layout.pos[e.target];
            if (!a || !b) return null;
            const on = lit(e.source) && lit(e.target);
            return (
              <line
                key={i}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={on ? "#3a3f47" : "#20242a"}
                strokeWidth={on ? 1 : 0.5}
              />
            );
          })}
          {layout.nodes.map((n) => {
            const p = layout.pos[n.id];
            if (!p) return null;
            const color = n.kind === "incident" || n.kind === "signature"
              ? SEV_COLOR[n.severity ?? ""] ?? KIND_COLOR[n.kind]
              : KIND_COLOR[n.kind];
            const on = lit(n.id);
            const anchor = n.kind === "incident" ? "end" : "start";
            const tx = n.kind === "incident" ? -10 : 10;
            return (
              <g
                key={n.id}
                opacity={on ? 1 : 0.25}
                onMouseEnter={() => setHover(n.id)}
                onMouseLeave={() => setHover(null)}
                style={{ cursor: "pointer" }}
              >
                <circle cx={p.x} cy={p.y} r={5} fill={color} />
                <text
                  x={p.x + tx}
                  y={p.y + 3}
                  textAnchor={anchor}
                  fontSize={11}
                  fill={hover === n.id ? "#e6e8ea" : "#9aa0a6"}
                >
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
