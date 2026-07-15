"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { dispatch, type Dispatch, type Responder, type Shift } from "@/lib/api";

const SEVERITY_COLOR: Record<string, string> = {
  critical: "text-red",
  high: "text-amber",
  medium: "text-cyan",
  low: "text-fg-muted",
  info: "text-fg-muted",
};

const SEVERITY_DOT: Record<string, string> = {
  critical: "bg-red",
  high: "bg-amber",
  medium: "bg-cyan",
  low: "bg-fg-muted",
  info: "bg-fg-muted",
};

const STATE_BADGE: Record<string, string> = {
  notified: "text-amber border-amber/50 bg-amber/10",
  acknowledged: "text-cyan border-cyan/40 bg-cyan/5",
  resolved: "text-green border-green/40 bg-green/5",
  escalated: "text-red border-red/50 bg-red/10",
  skipped: "text-fg-muted border-line bg-panel",
};

function timeAgo(iso?: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function StatTile({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
  return (
    <div className="border border-line bg-panel px-3 py-2">
      <div className="mono text-[10px] uppercase text-fg-muted">{label}</div>
      <div className={`mono mt-0.5 text-[18px] ${tone ?? "text-fg"}`}>{value}</div>
    </div>
  );
}

function slaOverdue(d: Dispatch): boolean {
  if (d.state !== "notified" || !d.notified_at) return false;
  const notified = new Date(d.notified_at).getTime();
  if (Number.isNaN(notified)) return false;
  return (Date.now() - notified) / 1000 > d.sla_ack_seconds;
}

function DispatchCard({ d }: { d: Dispatch }) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["dispatches"] });
    qc.invalidateQueries({ queryKey: ["dispatch-stats"] });
  };
  const ackM = useMutation({ mutationFn: () => dispatch.ack(d.id), onSuccess: invalidate });
  const resolveM = useMutation({ mutationFn: () => dispatch.resolve(d.id), onSuccess: invalidate });

  const sev = d.severity?.toLowerCase() ?? "info";
  const overdue = slaOverdue(d);
  const resolved = d.state === "resolved";

  return (
    <div className="border-b border-line/50 px-3 py-2.5">
      <div className="flex items-start gap-3">
        <span className={`mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ${SEVERITY_DOT[sev] ?? "bg-fg-muted"}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-[13px] text-fg">{d.signature_name ?? "Incident"}</span>
            <span className={`mono text-[10px] uppercase ${SEVERITY_COLOR[sev] ?? "text-fg-muted"}`}>{d.severity}</span>
            <span className={`mono rounded-[2px] border px-1.5 py-0.5 text-[9px] uppercase ${STATE_BADGE[d.state] ?? STATE_BADGE.skipped}`}>
              {d.state}
            </span>
            <span className="mono rounded-[2px] bg-raised px-1 text-[9px] uppercase text-fg-secondary">tier {d.tier}</span>
            {d.risk_score != null && (
              <span className="mono rounded-[2px] bg-raised px-1 text-[9px] text-cyan">risk {d.risk_score}</span>
            )}
            {overdue && (
              <span className="mono rounded-[2px] bg-red/20 px-1.5 py-0.5 text-[9px] uppercase text-red">SLA overdue</span>
            )}
          </div>
          {d.sitrep && <div className="mt-1 text-[12px] text-fg-secondary">{d.sitrep}</div>}
          <div className="mono mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-fg-muted">
            <span>{timeAgo(d.created_at)}</span>
            <span>·</span>
            <span>{d.responder_name ? `to ${d.responder_name}` : "unassigned"}</span>
            {d.ack_by && (
              <>
                <span>·</span>
                <span className="text-green">ack {d.ack_by}</span>
              </>
            )}
            {!overdue && d.state === "notified" && (
              <>
                <span>·</span>
                <span>SLA {d.sla_ack_seconds}s</span>
              </>
            )}
          </div>
          {!resolved && (
            <div className="mt-2 flex gap-2">
              {d.state === "notified" && (
                <button
                  disabled={ackM.isPending}
                  onClick={() => ackM.mutate()}
                  className="mono rounded-[3px] border border-line bg-raised px-2.5 py-1 text-[10px] uppercase text-cyan hover:bg-base disabled:opacity-40"
                >
                  {ackM.isPending ? "acking…" : "ack"}
                </button>
              )}
              <button
                disabled={resolveM.isPending}
                onClick={() => resolveM.mutate()}
                className="mono rounded-[3px] border border-line bg-raised px-2.5 py-1 text-[10px] uppercase text-green hover:bg-base disabled:opacity-40"
              >
                {resolveM.isPending ? "resolving…" : "resolve"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ShiftControl() {
  const qc = useQueryClient();
  const [operator, setOperator] = useState("");
  const shiftsQ = useQuery({
    queryKey: ["dispatch-shifts"],
    queryFn: ({ signal }) => dispatch.activeShifts(signal),
    refetchInterval: 5000,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["dispatch-shifts"] });
  const checkinM = useMutation({ mutationFn: () => dispatch.checkin(operator), onSuccess: invalidate });
  const checkoutM = useMutation({ mutationFn: () => dispatch.checkout(operator), onSuccess: invalidate });
  const shifts: Shift[] = shiftsQ.data ?? [];

  return (
    <div className="border border-line bg-panel px-3 py-2">
      <div className="mono text-[10px] uppercase text-fg-muted">Operator on shift</div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <input
          value={operator}
          onChange={(e) => setOperator(e.target.value)}
          placeholder="operator name"
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none"
        />
        <button
          disabled={!operator || checkinM.isPending}
          onClick={() => checkinM.mutate()}
          className="mono rounded-[3px] border border-line bg-raised px-2.5 py-1 text-[10px] uppercase text-green hover:bg-base disabled:opacity-40"
        >
          check in
        </button>
        <button
          disabled={!operator || checkoutM.isPending}
          onClick={() => checkoutM.mutate()}
          className="mono rounded-[3px] border border-line bg-raised px-2.5 py-1 text-[10px] uppercase text-amber hover:bg-base disabled:opacity-40"
        >
          check out
        </button>
      </div>
      <div className="mono mt-2 flex flex-wrap gap-1.5 text-[10px]">
        {shifts.length === 0 && <span className="text-fg-muted">no operators checked in</span>}
        {shifts.map((s) => (
          <span key={s.id} className="flex items-center gap-1 rounded-[2px] bg-raised px-1.5 py-0.5 text-fg-secondary">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-green" />
            {s.operator}
          </span>
        ))}
      </div>
    </div>
  );
}

function RespondersRail() {
  const q = useQuery({
    queryKey: ["dispatch-responders"],
    queryFn: ({ signal }) => dispatch.responders(signal),
    refetchInterval: 10000,
  });
  const responders: Responder[] = q.data ?? [];
  return (
    <div className="w-[300px] shrink-0 overflow-auto border-l border-line">
      <div className="border-b border-line px-3 py-2">
        <div className="text-[13px] font-semibold text-fg">Responder Roster</div>
        <div className="text-[11px] text-fg-muted">On-call responders and channels.</div>
      </div>
      {responders.length === 0 && (
        <div className="mono px-3 py-2 text-[11px] text-fg-muted">no responders configured</div>
      )}
      {responders.map((r) => (
        <div key={r.id} className="border-b border-line/40 px-3 py-2">
          <div className="flex items-center gap-2">
            <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${r.active ? "bg-green" : "bg-fg-muted"}`} />
            <span className="truncate text-[12px] text-fg">{r.name}</span>
            <span className="mono ml-auto text-[9px] uppercase text-fg-muted">{r.role}</span>
          </div>
          {(r.channels ?? []).length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {(r.channels ?? []).map((c) => (
                <span key={c} className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[9px] uppercase text-fg-secondary">
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function DispatchScreen() {
  const statsQ = useQuery({
    queryKey: ["dispatch-stats"],
    queryFn: ({ signal }) => dispatch.stats(signal),
    refetchInterval: 5000,
  });
  const listQ = useQuery({
    queryKey: ["dispatches"],
    queryFn: ({ signal }) => dispatch.list(undefined, signal),
    refetchInterval: 4000,
  });

  const stats: Record<string, number> = statsQ.data ?? {};
  const dispatches: Dispatch[] = listQ.data ?? [];

  return (
    <div className="flex h-full overflow-hidden">
      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">SOC Dispatch</div>
          <div className="text-[11px] text-fg-muted">Confirmed high/critical incidents dispatched to responders.</div>
        </div>

        <div className="border-b border-line p-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <StatTile label="dispatched" value={stats.dispatched ?? 0} />
            <StatTile label="open" value={stats.open ?? 0} tone="text-amber" />
            <StatTile label="acked" value={stats.acked ?? 0} tone="text-cyan" />
            <StatTile label="resolved" value={stats.resolved ?? 0} tone="text-green" />
            <StatTile label="escalated" value={stats.escalated ?? 0} tone="text-red" />
          </div>
          <div className="mt-3">
            <ShiftControl />
          </div>
        </div>

        <div className="flex-1 overflow-auto">
          {listQ.isLoading && <div className="p-3 text-[12px] text-fg-muted">loading dispatches…</div>}
          {!listQ.isLoading && dispatches.length === 0 && (
            <div className="mono p-4 text-[12px] text-fg-muted">
              no active dispatches — confirmed high/critical incidents will appear here
            </div>
          )}
          {dispatches.map((d) => (
            <DispatchCard key={d.id} d={d} />
          ))}
        </div>
      </div>

      <RespondersRail />
    </div>
  );
}
