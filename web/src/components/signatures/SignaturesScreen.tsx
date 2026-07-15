"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { core, type Sig } from "@/lib/api";
import { cn } from "@/lib/cn";

const SEV_TEXT: Record<string, string> = {
  critical: "text-red",
  high: "text-amber",
  medium: "text-cyan",
  low: "text-fg-muted",
  info: "text-fg-muted",
};

function OpenVocabBuilder({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  return (
    <div className="border-b border-line bg-panel px-3 py-2">
      <div className="mb-1.5 text-[10px] uppercase tracking-wide text-fg-muted">
        Open-vocabulary signature (operator-defined target)
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="signature name"
          className="w-40 rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none placeholder:text-fg-muted"
        />
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="target prompt, e.g. person on a ladder, traffic cone"
          className="min-w-[280px] flex-1 rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none placeholder:text-fg-muted"
        />
        <button
          disabled={busy || !name || !prompt}
          onClick={async () => {
            setBusy(true);
            setMsg("");
            try {
              await core.createOpenVocab({ name, prompt, severity: "medium" });
              setName("");
              setPrompt("");
              setMsg("created");
              onCreated();
            } catch (e) {
              setMsg(String(e));
            } finally {
              setBusy(false);
            }
          }}
          className="mono rounded-[2px] bg-raised px-2.5 py-1 text-[11px] text-cyan hover:text-fg disabled:opacity-40"
        >
          create
        </button>
        {msg && <span className="mono text-[11px] text-green">{msg}</span>}
      </div>
    </div>
  );
}

export function SignaturesScreen() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["signatures"], queryFn: ({ signal }) => core.signatures(signal) });
  const [filter, setFilter] = useState("");
  const sigs = (data ?? []) as Sig[];

  const cats = useMemo(() => Array.from(new Set(sigs.map((s) => s.category))).sort(), [sigs]);
  const [cat, setCat] = useState<string | null>(null);
  const shown = sigs.filter(
    (s) => (!cat || s.category === cat) && s.name.toLowerCase().includes(filter.toLowerCase()),
  );

  async function toggle(s: Sig) {
    await core.patchSignature(s.id, { enabled: !s.enabled });
    qc.invalidateQueries({ queryKey: ["signatures"] });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line bg-panel px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-secondary">
          Signature Library
        </span>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter..."
          className="ml-2 w-40 rounded-[3px] border border-line bg-base px-2 py-0.5 text-[12px] text-fg outline-none placeholder:text-fg-muted"
        />
        <span className="mono ml-auto text-[11px] text-fg-muted">
          {shown.length} / {sigs.length} ({sigs.filter((s) => s.enabled).length} enabled)
        </span>
      </div>

      <OpenVocabBuilder onCreated={() => qc.invalidateQueries({ queryKey: ["signatures"] })} />

      <div className="flex min-h-0 flex-1">
        <div className="w-44 shrink-0 overflow-auto border-r border-line bg-panel py-1">
          <button
            onClick={() => setCat(null)}
            className={cn("block w-full px-3 py-1 text-left text-[12px]", !cat ? "bg-raised text-fg" : "text-fg-muted hover:text-fg")}
          >
            all categories
          </button>
          {cats.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              className={cn("block w-full px-3 py-1 text-left text-[12px]", cat === c ? "bg-raised text-fg" : "text-fg-muted hover:text-fg")}
            >
              {c}
            </button>
          ))}
        </div>

        <div className="min-w-0 flex-1 overflow-auto">
          {shown.map((s) => (
            <div key={s.id} className="flex items-center gap-3 border-b border-line-soft px-3 py-1.5">
              <button
                onClick={() => toggle(s)}
                className={cn(
                  "relative h-4 w-7 shrink-0 rounded-full transition-colors",
                  s.enabled ? "bg-green/40" : "bg-raised",
                )}
                title={s.enabled ? "enabled" : "disabled"}
              >
                <span
                  className={cn(
                    "absolute top-0.5 h-3 w-3 rounded-full transition-all",
                    s.enabled ? "left-3.5 bg-green" : "left-0.5 bg-fg-muted",
                  )}
                />
              </button>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[12px] text-fg">{s.name}</div>
                <div className="mono truncate text-[10px] text-fg-muted">
                  {s.category} / {s.detection_method} / fired {s.detection_count}x
                </div>
              </div>
              <span className={cn("mono text-[10px] uppercase", SEV_TEXT[s.severity])}>{s.severity}</span>
              {s.source !== "built_in" && (
                <span className="mono rounded-[2px] bg-raised px-1 text-[9px] uppercase text-cyan">{s.source}</span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
