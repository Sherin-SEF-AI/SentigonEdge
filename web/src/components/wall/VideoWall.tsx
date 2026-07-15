"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { wallPriority } from "@/lib/api";
import { useStreams } from "@/hooks/useIngest";

import { VideoTile } from "./VideoTile";

function gridColumns(n: number): number {
  if (n <= 1) return 1;
  if (n <= 4) return 2;
  if (n <= 9) return 3;
  return 4;
}

export function VideoWall() {
  const { data: streams, isLoading, error } = useStreams();
  const [agentic, setAgentic] = useState(true);
  const prioQ = useQuery({
    queryKey: ["wall-priority"],
    queryFn: ({ signal }) => wallPriority(signal),
    refetchInterval: 4000,
    enabled: agentic,
  });

  const prio = useMemo(() => {
    const m = new Map<string, { rank: number; score: number; open: number; maxRisk: number }>();
    for (const c of prioQ.data?.cameras ?? [])
      m.set(c.camera_id, { rank: c.rank, score: c.score, open: c.open_incidents, maxRisk: c.max_risk });
    return m;
  }, [prioQ.data]);

  // in agentic mode, order streams by priority score (highest-threat first)
  const ordered = useMemo(() => {
    const list = [...(streams ?? [])];
    if (!agentic) return list;
    return list.sort(
      (a, b) => (prio.get(b.camera_id)?.score ?? 0) - (prio.get(a.camera_id)?.score ?? 0),
    );
  }, [streams, agentic, prio]);

  const cols = useMemo(() => gridColumns(ordered.length), [ordered]);
  const topId =
    agentic && ordered.length > 0 && (prio.get(ordered[0].camera_id)?.score ?? 0) > 0
      ? ordered[0].camera_id
      : null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line bg-panel px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
          {agentic ? "Agentic Video Wall" : "Live Video Wall"}
        </span>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setAgentic((v) => !v)}
            className={`mono rounded-[2px] px-1.5 py-0.5 text-[10px] uppercase ${
              agentic ? "bg-cyan/20 text-cyan" : "bg-raised text-fg-muted hover:text-fg"
            }`}
          >
            agentic {agentic ? "on" : "off"}
          </button>
          <span className="mono text-[11px] text-fg-muted">{ordered.length} streams / WebRTC</span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-2">
        {error ? (
          <div className="flex h-full items-center justify-center text-[13px] text-red">
            ingest unreachable ({String(error)})
          </div>
        ) : isLoading ? (
          <div className="flex h-full items-center justify-center text-[13px] text-fg-muted">
            loading streams...
          </div>
        ) : ordered.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[13px] text-fg-muted">
            no active streams. run `make samples` then `make ingest`.
          </div>
        ) : (
          <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
            {ordered.map((s) => {
              const p = prio.get(s.camera_id);
              const isTop = s.camera_id === topId;
              return (
                <div
                  key={s.camera_id}
                  className={`relative ${isTop ? "col-span-2 row-span-2 ring-2 ring-red" : ""}`}
                >
                  <VideoTile stream={s} />
                  {agentic && p && p.score > 0 && (
                    <div
                      className={`mono absolute left-1 top-1 z-10 rounded-[2px] px-1.5 py-0.5 text-[9px] uppercase ${
                        p.maxRisk >= 80
                          ? "bg-red/80 text-white"
                          : p.maxRisk >= 60
                            ? "bg-amber/80 text-black"
                            : "bg-cyan/70 text-black"
                      }`}
                    >
                      #{p.rank} · risk {p.maxRisk} · {p.open} open
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
