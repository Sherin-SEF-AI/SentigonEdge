"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { core } from "@/lib/api";
import { cn } from "@/lib/cn";

const SEV_DOT: Record<string, string> = { critical: "bg-red", high: "bg-amber", medium: "bg-cyan", low: "bg-fg-muted" };

function CaseDetail({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["case", id], queryFn: ({ signal }) => core.caseDetail(id, signal) });
  const [exp, setExp] = useState<Record<string, unknown> | null>(null);
  if (!data) return <div className="p-4 text-[12px] text-fg-muted">loading...</div>;
  const d = data as Record<string, unknown>;
  const incidents = (d.incidents ?? []) as Record<string, unknown>[];
  return (
    <div className="flex h-full flex-col overflow-auto">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <div>
          <div className="text-[14px] font-semibold text-fg">{String(d.title)}</div>
          <div className="mono text-[11px] text-fg-muted">
            {String(d.status)} / priority {String(d.priority)} / {incidents.length} incidents
          </div>
        </div>
        <button
          onClick={async () => setExp(await core.caseExport(id))}
          className="mono rounded-[2px] bg-raised px-2.5 py-1 text-[11px] text-cyan hover:text-fg"
        >
          export (chain of custody)
        </button>
      </div>
      {exp && (
        <div className="border-b border-line bg-panel px-3 py-2">
          <div className="mono text-[11px] text-fg-secondary">
            evidence_chain_verified:{" "}
            <span className={exp.evidence_chain_verified ? "text-green" : "text-red"}>
              {String(exp.evidence_chain_verified)}
            </span>{" "}
            / manifest entries: {(exp.evidence_manifest as unknown[])?.length ?? 0}
          </div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-2 p-3 md:grid-cols-3">
        {incidents.map((i) => (
          <div key={String(i.id)} className="overflow-hidden rounded-[3px] border border-line bg-panel">
            <div className="aspect-video bg-black">
              {i.snapshot_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={String(i.snapshot_url)} alt="" className="h-full w-full object-cover" />
              ) : null}
            </div>
            <div className="flex items-center gap-1.5 px-2 py-1.5">
              <span className={cn("h-1.5 w-1.5 rounded-full", SEV_DOT[String(i.severity)])} />
              <div className="min-w-0">
                <div className="truncate text-[11px] text-fg">{String(i.title)}</div>
                <div className="mono truncate text-[10px] text-fg-muted">{String(i.camera)}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CasesScreen() {
  const { data: cases } = useQuery({ queryKey: ["cases"], queryFn: ({ signal }) => core.cases(signal), refetchInterval: 5000 });
  const list = cases ?? [];
  const [selected, setSelected] = useState<string | null>(null);
  const active = selected ?? list[0]?.id ?? null;

  return (
    <div className="grid h-full grid-cols-[300px_minmax(0,1fr)]">
      <div className="flex flex-col border-r border-line">
        <div className="border-b border-line bg-panel px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
          Cases ({list.length})
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          {list.length === 0 ? (
            <div className="p-3 text-[12px] text-fg-muted">
              no cases yet. group incidents into a case via the API (POST /cases).
            </div>
          ) : (
            list.map((c) => (
              <button
                key={c.id}
                onClick={() => setSelected(c.id)}
                className={cn("block w-full border-b border-line-soft px-3 py-2 text-left hover:bg-panel", active === c.id && "bg-raised")}
              >
                <div className="truncate text-[12px] text-fg">{c.title}</div>
                <div className="mono text-[10px] text-fg-muted">
                  {c.priority} / {c.incidents} incidents / {c.status}
                </div>
              </button>
            ))
          )}
        </div>
      </div>
      <div className="min-w-0 bg-base">
        {active ? <CaseDetail id={active} /> : <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">select a case</div>}
      </div>
    </div>
  );
}
