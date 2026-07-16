"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useIncident, useIncidentAction, useIncidents } from "@/hooks/useCore";
import { core, type Incident } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useAuth } from "@/store/auth";
import { useUI } from "@/store/ui";

const SEV_DOT: Record<string, string> = {
  critical: "bg-red",
  high: "bg-amber",
  medium: "bg-cyan",
  low: "bg-fg-muted",
  info: "bg-fg-muted",
};
const SEV_TEXT: Record<string, string> = {
  critical: "text-red",
  high: "text-amber",
  medium: "text-cyan",
  low: "text-fg-muted",
  info: "text-fg-muted",
};
const STATUS_TEXT: Record<string, string> = {
  new: "text-red",
  ack: "text-amber",
  escalated: "text-magenta",
  resolved: "text-green",
  false_positive: "text-fg-muted",
};
const VERDICT_TONE: Record<string, string> = {
  confirmed: "bg-red/20 text-red",
  rejected: "bg-green/15 text-green",
  unverified: "bg-amber/15 text-amber",
};

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

const FILTERS = [
  { key: undefined, label: "All" },
  { key: "new", label: "New" },
  { key: "ack", label: "Ack" },
  { key: "escalated", label: "Escalated" },
  { key: "resolved", label: "Resolved" },
];

type GBox = { label: string; score: number; box: [number, number, number, number] };

// Draws grounded ("find and box it") boxes over the incident snapshot. Coords are
// normalized 0..1 against the same frame the model localized, and the snapshot img
// uses object-contain at full width (no letterbox), so percentages map 1:1.
function GroundingOverlay({ boxes }: { boxes: GBox[] }) {
  return (
    <div className="pointer-events-none absolute inset-0">
      {boxes.map((b, i) => {
        const [x1, y1, x2, y2] = b.box;
        return (
          <div
            key={i}
            className="absolute border border-cyan shadow-[0_0_0_1px_rgba(0,0,0,0.5)]"
            style={{
              left: `${x1 * 100}%`,
              top: `${y1 * 100}%`,
              width: `${(x2 - x1) * 100}%`,
              height: `${(y2 - y1) * 100}%`,
            }}
          >
            <span className="mono absolute -top-[13px] left-0 whitespace-nowrap bg-cyan px-1 text-[9px] font-semibold text-base">
              {b.label} {Number(b.score).toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function DetailPane({ id }: { id: string }) {
  const { data } = useIncident(id);
  const action = useIncidentAction();
  const select = useUI((s) => s.select);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const map: Record<string, string> = { a: "ack", e: "escalate", r: "resolve", f: "false" };
      const act = map[e.key.toLowerCase()];
      const tag = (e.target as HTMLElement)?.tagName;
      if (act && tag !== "INPUT" && tag !== "TEXTAREA") {
        e.preventDefault();
        action.mutate({ id, action: act });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [id, action]);

  if (!data) return <div className="p-4 text-[12px] text-fg-muted">loading incident...</div>;
  const d = data as Record<string, unknown>;
  const sev = String(d.severity);
  const ctx = (d.attributes ?? {}) as Record<string, unknown>;
  const boxes: GBox[] = Array.isArray(ctx.boxes) ? (ctx.boxes as GBox[]) : [];
  const timeline = (d.timeline ?? []) as { to: string; note: string; ts: string }[];
  const verdict = d.verdict ? String(d.verdict) : null;
  const reasoning = ((d.reasoning_trace ?? {}) as Record<string, unknown>).reasoning as
    | string
    | undefined;

  return (
    <div className="flex h-full flex-col overflow-auto">
      <div className="flex items-start justify-between gap-2 border-b border-line px-3 py-2">
        <div>
          <div className={cn("text-[14px] font-semibold", SEV_TEXT[sev])}>{String(d.title)}</div>
          <div className="mono mt-0.5 text-[11px] text-fg-muted">
            {String(d.signature)} / {String(d.camera)} / conf {Number(d.confidence).toFixed(2)}
          </div>
        </div>
        <span className={cn("mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[10px] uppercase", STATUS_TEXT[String(d.status)])}>
          {String(d.status)}
        </span>
      </div>

      {d.snapshot_url ? (
        <div className="relative border-b border-line bg-black">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={String(d.snapshot_url)} alt="incident snapshot" className="w-full object-contain" />
          {boxes.length > 0 && <GroundingOverlay boxes={boxes} />}
        </div>
      ) : (
        <div className="flex h-40 items-center justify-center border-b border-line text-[12px] text-fg-muted">
          no snapshot
        </div>
      )}

      <div className="border-b border-line px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-fg-muted">Sentigon Reason (VLM)</span>
          {verdict ? (
            <span className={cn("mono rounded-[2px] px-1.5 py-0.5 text-[10px] font-semibold uppercase", VERDICT_TONE[verdict])}>
              {verdict}
            </span>
          ) : (
            <span className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[10px] uppercase text-fg-muted">
              verifying...
            </span>
          )}
        </div>
        {d.sitrep ? (
          <div className="mt-1.5 text-[13px] leading-snug text-fg">{String(d.sitrep)}</div>
        ) : null}
        {reasoning ? (
          <div className="mt-1 text-[11px] leading-snug text-fg-secondary">{reasoning}</div>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-px bg-line-soft">
        {Object.entries(ctx).filter(([k]) => k !== "boxes").map(([k, v]) => (
          <div key={k} className="bg-panel px-3 py-1.5">
            <div className="text-[10px] uppercase text-fg-muted">{k}</div>
            <div className="mono text-[12px] text-fg">{typeof v === "object" ? JSON.stringify(v) : String(v)}</div>
          </div>
        ))}
      </div>

      <div className="border-t border-line px-3 py-2">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-fg-muted">Timeline</div>
        {timeline.map((t, i) => (
          <div key={i} className="mono flex gap-2 text-[11px] text-fg-secondary">
            <span className="text-fg-muted">{new Date(t.ts).toLocaleTimeString([], { hour12: false })}</span>
            <span className={STATUS_TEXT[t.to] ?? "text-fg"}>{t.to}</span>
            {t.note && <span className="text-fg-muted">{t.note}</span>}
          </div>
        ))}
      </div>

      <div className="mt-auto grid grid-cols-4 gap-px border-t border-line bg-line-soft">
        {[
          { a: "ack", label: "Ack (A)", tone: "text-amber" },
          { a: "escalate", label: "Escalate (E)", tone: "text-magenta" },
          { a: "resolve", label: "Resolve (R)", tone: "text-green" },
          { a: "false", label: "False (F)", tone: "text-fg-muted" },
        ].map((b) => (
          <button
            key={b.a}
            onClick={() => action.mutate({ id, action: b.a })}
            className={cn("bg-panel py-2 text-[11px] font-medium hover:bg-raised", b.tone)}
          >
            {b.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function IncidentQueue() {
  const [filter, setFilter] = useState<string | undefined>(undefined);
  const { data: incidents } = useIncidents(filter);
  const [selected, setSelected] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const select = useUI((s) => s.select);
  const setModal = useAuth((s) => s.setModal);
  const qc = useQueryClient();

  const list = (incidents ?? []) as Incident[];
  const active = selected ?? list[0]?.id ?? null;

  const bulk = useMutation({
    mutationFn: ({ action }: { action: string }) => core.bulkAction([...checked], action),
    onSuccess: () => {
      setChecked(new Set());
      qc.invalidateQueries({ queryKey: ["incidents"] });
      qc.invalidateQueries({ queryKey: ["core-summary"] });
    },
    onError: (e: Error) => {
      if (e.message.startsWith("401")) setModal(true);
    },
  });

  const toggle = (id: string) =>
    setChecked((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  return (
    <div className="grid h-full grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex min-w-0 flex-col border-r border-line">
        <div className="flex items-center gap-1 border-b border-line bg-panel px-2 py-1.5">
          <span className="mr-2 text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
            Incident Queue
          </span>
          {FILTERS.map((f) => (
            <button
              key={f.label}
              onClick={() => setFilter(f.key)}
              className={cn(
                "rounded-[2px] px-2 py-0.5 text-[11px]",
                filter === f.key ? "bg-raised text-fg" : "text-fg-muted hover:text-fg-secondary",
              )}
            >
              {f.label}
            </button>
          ))}
          <span className="mono ml-auto text-[11px] text-fg-muted">{list.length}</span>
        </div>
        {checked.size > 0 && (
          <div className="flex items-center gap-2 border-b border-line bg-raised px-3 py-1.5">
            <span className="mono text-[11px] text-cyan">{checked.size} selected</span>
            {[
              { a: "ack", label: "Ack", tone: "text-amber" },
              { a: "resolve", label: "Resolve", tone: "text-green" },
              { a: "false", label: "False positive", tone: "text-fg-muted" },
            ].map((b) => (
              <button
                key={b.a}
                disabled={bulk.isPending}
                onClick={() => bulk.mutate({ action: b.a })}
                className={cn("mono rounded-[2px] bg-panel px-2 py-0.5 text-[11px] hover:bg-base disabled:opacity-40", b.tone)}
              >
                {b.label} ({checked.size})
              </button>
            ))}
            <button onClick={() => setChecked(new Set())} className="mono ml-auto text-[11px] text-fg-muted hover:text-fg">
              clear
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto">
          {list.length === 0 ? (
            <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">
              no incidents
            </div>
          ) : (
            list.map((inc) => (
              <button
                key={inc.id}
                onClick={() => {
                  setSelected(inc.id);
                  select({ kind: "incident", id: inc.id, label: inc.title, data: { severity: inc.severity, camera: inc.camera, signature: inc.signature, status: inc.status } });
                }}
                className={cn(
                  "flex w-full items-center gap-2 border-b border-line-soft px-3 py-2 text-left hover:bg-panel",
                  active === inc.id && "bg-raised",
                )}
              >
                <span
                  role="checkbox"
                  aria-checked={checked.has(inc.id)}
                  tabIndex={-1}
                  onClick={(e) => {
                    e.stopPropagation();
                    toggle(inc.id);
                  }}
                  className={cn(
                    "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[2px] border",
                    checked.has(inc.id) ? "border-cyan bg-cyan/30 text-cyan" : "border-line",
                  )}
                >
                  {checked.has(inc.id) ? "✓" : ""}
                </span>
                <span className={cn("h-2 w-2 shrink-0 rounded-full", SEV_DOT[inc.severity])} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] text-fg">{inc.title}</div>
                  <div className="mono truncate text-[10px] text-fg-muted">
                    {inc.signature} / {inc.camera}
                  </div>
                </div>
                <span className={cn("mono text-[10px] uppercase", STATUS_TEXT[inc.status])}>{inc.status}</span>
                <span className="mono w-8 shrink-0 text-right text-[10px] text-fg-muted">{ago(inc.created_at)}</span>
              </button>
            ))
          )}
        </div>
      </div>
      <div className="min-w-0 bg-base">
        {active ? (
          <DetailPane id={active} />
        ) : (
          <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">
            select an incident
          </div>
        )}
      </div>
    </div>
  );
}
