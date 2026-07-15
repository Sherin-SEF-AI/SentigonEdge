"use client";

import { ChevronRight, X } from "lucide-react";

import { useUI } from "@/store/ui";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line-soft px-3 py-1.5">
      <span className="text-[11px] uppercase tracking-wide text-fg-muted">{k}</span>
      <span className="mono text-[12px] text-fg">{v}</span>
    </div>
  );
}

export function InspectorDock() {
  const inspectorOpen = useUI((s) => s.inspectorOpen);
  const selection = useUI((s) => s.selection);
  const clear = useUI((s) => s.clearSelection);
  if (!inspectorOpen) return null;

  const data = (selection.data ?? {}) as Record<string, unknown>;

  return (
    <aside className="flex min-w-0 flex-col overflow-hidden border-l border-line bg-panel">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
          Inspector
        </span>
        {selection.id && (
          <button className="text-fg-muted hover:text-fg" onClick={clear} title="Clear selection">
            <X size={13} />
          </button>
        )}
      </div>

      {!selection.id ? (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-[12px] text-fg-muted">
          Select a camera, incident, or zone to inspect its properties.
        </div>
      ) : (
        <div className="flex-1 overflow-auto">
          <div className="flex items-center gap-1.5 bg-raised px-3 py-2">
            <ChevronRight size={13} className="text-fg-muted" />
            <span className="text-[12px] font-medium text-fg">{selection.label ?? selection.id}</span>
            <span className="mono ml-auto rounded-[2px] bg-panel px-1.5 py-0.5 text-[10px] uppercase text-cyan">
              {selection.kind}
            </span>
          </div>
          <div>
            {Object.entries(data).map(([k, v]) => (
              <Row key={k} k={k} v={typeof v === "object" ? JSON.stringify(v) : String(v)} />
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
