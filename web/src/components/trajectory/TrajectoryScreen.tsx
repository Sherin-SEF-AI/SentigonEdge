"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { reid, type ReidTrack } from "@/lib/api";

const CAM_COLOR: Record<string, string> = {
  "Retail Aisle": "text-cyan",
  "Warehouse Floor": "text-amber",
  "Street Perimeter": "text-magenta",
  "Entrance Hall": "text-green",
};

function scoreColor(s: number): string {
  if (s >= 0.8) return "text-green";
  if (s >= 0.65) return "text-amber";
  return "text-fg-muted";
}

function hhmmss(ts?: string | null): string {
  return ts ? ts.slice(11, 19) : "--";
}

export function TrajectoryScreen() {
  const [sel, setSel] = useState<ReidTrack | null>(null);
  const tracksQ = useQuery({ queryKey: ["reid-tracks"], queryFn: ({ signal }) => reid.tracks(40, signal) });
  const trajQ = useQuery({
    queryKey: ["reid-traj", sel?.camera_id, sel?.track_id],
    queryFn: ({ signal }) => reid.trajectory(sel!.camera_id, sel!.track_id, 0.0, signal),
    enabled: !!sel,
  });

  const tracks = tracksQ.data?.tracks ?? [];
  const traj = trajQ.data;

  return (
    <div className="flex h-full overflow-hidden">
      {/* track picker */}
      <div className="w-[300px] shrink-0 overflow-auto border-r border-line">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">Entity Trajectories</div>
          <div className="text-[11px] text-fg-muted">
            Pick a tracked subject to reconstruct its cross-camera path.
          </div>
        </div>
        {tracksQ.isLoading && <div className="p-3 text-[12px] text-fg-muted">loading tracks...</div>}
        {tracks.map((t) => {
          const active = sel?.camera_id === t.camera_id && sel?.track_id === t.track_id;
          return (
            <button
              key={`${t.camera_id}-${t.track_id}`}
              onClick={() => setSel(t)}
              className={`flex w-full items-center justify-between gap-2 border-b border-line/50 px-3 py-2 text-left hover:bg-panel ${
                active ? "bg-panel" : ""
              }`}
            >
              <div className="min-w-0">
                <div className={`text-[12px] ${CAM_COLOR[t.camera] ?? "text-fg"}`}>{t.camera}</div>
                <div className="mono text-[10px] text-fg-muted">
                  track#{t.track_id} · {t.object_class}
                </div>
              </div>
              <div className="mono shrink-0 text-right text-[10px] text-fg-muted">
                <div className="text-fg-secondary">x{t.appearances}</div>
                <div>{hhmmss(t.first_ts)}</div>
              </div>
            </button>
          );
        })}
      </div>

      {/* trajectory view */}
      <div className="flex-1 overflow-auto p-4">
        {!sel && (
          <div className="mono flex h-full items-center justify-center text-[12px] text-fg-muted">
            select a subject on the left
          </div>
        )}
        {sel && trajQ.isLoading && (
          <div className="mono text-[12px] text-fg-muted">reconstructing trajectory...</div>
        )}
        {traj?.found && traj.query && (
          <>
            {/* subject + continuity */}
            <div className="mb-4 border border-line bg-panel p-3">
              <div className="flex items-center justify-between">
                <div className={`text-[14px] font-semibold ${CAM_COLOR[traj.query.camera] ?? "text-fg"}`}>
                  {traj.query.camera} · track#{traj.query.track_id}
                </div>
                <span className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[10px] uppercase text-cyan">
                  {traj.query.object_class}
                </span>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-3 text-[12px]">
                <div>
                  <div className="text-[10px] uppercase text-fg-muted">appearances</div>
                  <div className="mono text-fg">{traj.query.appearances}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase text-fg-muted">single-cam continuity</div>
                  <div className="mono text-fg">{(traj.query.continuity_cohesion * 100).toFixed(1)}%</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase text-fg-muted">observed span</div>
                  <div className="mono text-fg">
                    {hhmmss(traj.query.first_ts)} → {hhmmss(traj.query.last_ts)}
                  </div>
                </div>
              </div>
              <div className="mt-2 text-[10px] text-fg-muted">
                Continuity = mean cosine of each appearance to the track centroid (ByteTrack persistence
                quality). Cross-camera links below are appearance-similarity candidates; these clips are
                disjoint scenes, so true same-identity matches (≥0.8) are not expected.
              </div>
            </div>

            {/* cross-camera path */}
            <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
              Cross-camera path (by time)
            </div>
            <div className="space-y-2">
              {(traj.timeline ?? []).map((e, i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="mono w-16 shrink-0 text-right text-[11px] text-fg-muted">
                    {hhmmss(e.ts)}
                  </div>
                  <div
                    className={`h-2 w-2 shrink-0 rounded-full ${
                      e.kind === "origin" ? "bg-cyan" : "bg-fg-muted"
                    }`}
                  />
                  <div className="flex flex-1 items-center justify-between border border-line bg-panel px-3 py-1.5">
                    <span className={`text-[12px] ${CAM_COLOR[e.camera] ?? "text-fg"}`}>
                      {e.camera} · track#{e.track_id}
                      {e.kind === "origin" && (
                        <span className="mono ml-2 text-[10px] uppercase text-cyan">origin</span>
                      )}
                    </span>
                    <span className={`mono text-[12px] ${scoreColor(e.match_score)}`}>
                      {e.kind === "origin" ? "1.000" : e.match_score.toFixed(3)}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            {/* candidate table */}
            <div className="mt-5 mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
              Best match per other camera
            </div>
            <div className="space-y-1">
              {(traj.cross_camera_matches ?? []).length === 0 && (
                <div className="mono text-[12px] text-fg-muted">no cross-camera candidates above threshold</div>
              )}
              {(traj.cross_camera_matches ?? [])
                .filter((m, i, arr) => arr.findIndex((x) => x.camera === m.camera) === i)
                .map((m) => (
                  <div
                    key={`${m.camera_id}-${m.track_id}`}
                    className="flex items-center justify-between border border-line bg-panel px-3 py-1.5"
                  >
                    <span className={`text-[12px] ${CAM_COLOR[m.camera] ?? "text-fg"}`}>
                      {m.camera} · track#{m.track_id} · {m.object_class}
                    </span>
                    <span className={`mono text-[12px] ${scoreColor(m.match_score)}`}>
                      {m.match_score.toFixed(3)}
                    </span>
                  </div>
                ))}
            </div>
          </>
        )}
        {traj && !traj.found && (
          <div className="mono text-[12px] text-fg-muted">{traj.reason ?? "no trajectory"}</div>
        )}
      </div>
    </div>
  );
}
