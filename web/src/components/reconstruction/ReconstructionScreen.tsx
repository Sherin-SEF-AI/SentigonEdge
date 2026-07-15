"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  core,
  incidentReconstruction,
  incidentSnapshotUrl,
  type Incident,
  type ReconEntry,
} from "@/lib/api";
import { useUI } from "@/store/ui";

const WINDOWS = [60, 180, 600];

function hhmmss(ts?: string | null): string {
  return ts ? ts.slice(11, 19) : "--";
}

function sevColor(sev?: string): string {
  switch ((sev ?? "").toLowerCase()) {
    case "critical":
    case "high":
      return "text-red";
    case "medium":
      return "text-amber";
    default:
      return "text-green";
  }
}

function scoreColor(s?: number): string {
  if (s === undefined) return "text-fg-muted";
  if (s >= 0.86) return "text-green";
  if (s >= 0.7) return "text-amber";
  return "text-fg-muted";
}

// visual marker per timeline entry type
function entryDot(e: ReconEntry): string {
  if (e.type === "incident") return "bg-cyan";
  if (e.type === "appearance") return "bg-green";
  if (e.kind === "handoff") return "bg-magenta";
  return "bg-fg-muted";
}

function TimelineRow({ e }: { e: ReconEntry }) {
  const anchor = e.type === "incident";
  return (
    <div className="flex items-stretch gap-3">
      <div className="mono w-16 shrink-0 pt-1.5 text-right text-[11px] text-fg-muted">
        {hhmmss(e.ts)}
      </div>
      <div className="flex flex-col items-center">
        <div className={`mt-2 h-2.5 w-2.5 shrink-0 rounded-full ${entryDot(e)}`} />
        <div className="w-px flex-1 bg-line" />
      </div>
      <div
        className={`mb-2 flex-1 border px-3 py-1.5 ${
          anchor ? "border-cyan/60 bg-cyan/5" : "border-line bg-panel"
        }`}
      >
        <div className="flex items-center justify-between gap-2">
          <span className="text-[12px] text-fg">
            <span className="text-fg-secondary">{e.camera ?? "?"}</span>
            {e.signature && <span className="ml-2">{e.signature}</span>}
            {e.type === "appearance" && (
              <span className="mono ml-2 text-[10px] uppercase text-green">re-identified</span>
            )}
            {anchor && (
              <span className="mono ml-2 rounded-[2px] bg-cyan/20 px-1 text-[10px] uppercase text-cyan">
                anchor
              </span>
            )}
            {e.kind === "handoff" && (
              <span className="mono ml-2 text-[10px] uppercase text-magenta">handoff</span>
            )}
          </span>
          {e.match_score !== undefined ? (
            <span className={`mono text-[12px] ${scoreColor(e.match_score)}`}>
              {e.match_score.toFixed(3)}
            </span>
          ) : e.severity ? (
            <span className={`mono text-[10px] uppercase ${sevColor(e.severity)}`}>{e.severity}</span>
          ) : null}
        </div>
        {e.title && e.type !== "appearance" && (
          <div className="mt-0.5 text-[11px] text-fg-muted">{e.title}</div>
        )}
      </div>
    </div>
  );
}

export function ReconstructionScreen() {
  const selection = useUI((s) => s.selection);
  const [incidentId, setIncidentId] = useState<string | null>(
    selection.kind === "incident" ? selection.id : null,
  );
  const [windowS, setWindowS] = useState(180);
  const [blurFaces, setBlurFaces] = useState(true);
  const [snapOk, setSnapOk] = useState(true);

  // follow a globally-selected incident
  useEffect(() => {
    if (selection.kind === "incident" && selection.id) setIncidentId(selection.id);
  }, [selection]);

  // reset snapshot load state when the target incident changes
  useEffect(() => setSnapOk(true), [incidentId]);

  const listQ = useQuery({
    queryKey: ["recon-incidents"],
    queryFn: ({ signal }) => core.incidents(undefined, signal),
    refetchInterval: 15000,
  });
  const reconQ = useQuery({
    queryKey: ["reconstruction", incidentId, windowS],
    queryFn: ({ signal }) => incidentReconstruction(incidentId!, windowS, signal),
    enabled: !!incidentId,
  });

  const incidents: Incident[] = listQ.data ?? [];
  const recon = reconQ.data;

  return (
    <div className="flex h-full overflow-hidden">
      {/* incident picker */}
      <div className="w-[280px] shrink-0 overflow-auto border-r border-line">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">Incident Reconstruction</div>
          <div className="text-[11px] text-fg-muted">
            Pick an incident to replay its multi-camera timeline.
          </div>
        </div>
        {listQ.isLoading && <div className="p-3 text-[12px] text-fg-muted">loading incidents...</div>}
        {incidents.map((i) => (
          <button
            key={i.id}
            onClick={() => setIncidentId(i.id)}
            className={`flex w-full flex-col gap-0.5 border-b border-line/50 px-3 py-2 text-left hover:bg-panel ${
              incidentId === i.id ? "bg-panel" : ""
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[12px] text-fg">{i.title}</span>
              <span className={`mono shrink-0 text-[9px] uppercase ${sevColor(i.severity)}`}>
                {i.severity}
              </span>
            </div>
            <div className="mono text-[10px] text-fg-muted">
              {i.camera ?? "?"} · {hhmmss(i.created_at)}
            </div>
          </button>
        ))}
      </div>

      {/* reconstruction */}
      <div className="flex-1 overflow-auto p-4">
        {!incidentId && (
          <div className="mono flex h-full items-center justify-center text-[12px] text-fg-muted">
            select an incident on the left
          </div>
        )}
        {incidentId && reconQ.isLoading && (
          <div className="mono text-[12px] text-fg-muted">reconstructing timeline...</div>
        )}
        {recon && (
          <>
            {/* header */}
            <div className="mb-4 flex gap-4">
              <div className="relative h-[150px] w-[266px] shrink-0 overflow-hidden border border-line bg-raised">
                {snapOk ? (
                  <>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={incidentSnapshotUrl(recon.incident_id, { faces: blurFaces })}
                      alt="incident snapshot"
                      className="h-full w-full object-cover"
                      onError={() => setSnapOk(false)}
                    />
                    <button
                      onClick={() => setBlurFaces((v) => !v)}
                      className="mono absolute bottom-1 right-1 rounded-[2px] bg-black/70 px-1.5 py-0.5 text-[9px] uppercase text-fg-secondary hover:text-cyan"
                    >
                      faces: {blurFaces ? "blurred" : "raw"}
                    </button>
                  </>
                ) : (
                  <div className="mono flex h-full items-center justify-center text-[10px] uppercase text-fg-muted">
                    no snapshot
                  </div>
                )}
              </div>
              <div className="flex-1">
                <div className="mono text-[10px] uppercase tracking-[0.15em] text-fg-muted">
                  anchor {hhmmss(recon.anchor_ts)}
                </div>
                <div className="mt-1 grid grid-cols-2 gap-2 text-[12px]">
                  <Stat label="subject track" value={recon.subject_track ?? "n/a"} />
                  <Stat label="cross-cam re-ids" value={recon.counts.cross_camera_appearances} />
                  <Stat label="related incidents" value={recon.counts.related_incidents} />
                  <Stat label="recording segments" value={recon.counts.recording_segments} />
                </div>
                <div className="mt-2">
                  <div className="text-[10px] uppercase text-fg-muted">involved cameras</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {recon.involved_cameras.map((c) => (
                      <span key={c} className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[10px] text-fg-secondary">
                        {c}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* window selector */}
            <div className="mb-3 flex items-center gap-2">
              <span className="text-[10px] uppercase text-fg-muted">window</span>
              {WINDOWS.map((w) => (
                <button
                  key={w}
                  onClick={() => setWindowS(w)}
                  className={`mono rounded-[2px] px-2 py-0.5 text-[10px] ${
                    windowS === w ? "bg-cyan/20 text-cyan" : "bg-raised text-fg-muted hover:text-fg"
                  }`}
                >
                  ±{w}s
                </button>
              ))}
              {!recon.trajectory_found && (
                <span className="mono ml-2 text-[10px] text-fg-muted">
                  no ReID trajectory for this subject
                </span>
              )}
            </div>

            {/* timeline */}
            <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
              Timeline ({recon.timeline.length} events)
            </div>
            <div>
              {recon.timeline.map((e, i) => (
                <TimelineRow key={i} e={e} />
              ))}
              {recon.timeline.length === 0 && (
                <div className="mono text-[12px] text-fg-muted">no events in window</div>
              )}
            </div>

            {/* recording segments for replay */}
            {recon.recording_segments.length > 0 && (
              <>
                <div className="mt-5 mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">
                  Recording segments ({recon.recording_segments.length})
                </div>
                <div className="max-h-[220px] space-y-1 overflow-auto">
                  {recon.recording_segments.map((r, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between border border-line bg-panel px-3 py-1"
                    >
                      <span className="text-[11px] text-fg-secondary">{r.camera}</span>
                      <span className="mono text-[10px] text-fg-muted">
                        {hhmmss(r.start)} → {hhmmss(r.end)}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
        {reconQ.isError && (
          <div className="mono text-[12px] text-red">failed to load reconstruction</div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-[10px] uppercase text-fg-muted">{label}</div>
      <div className="mono text-fg">{value}</div>
    </div>
  );
}
