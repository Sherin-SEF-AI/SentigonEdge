"use client";

import { useEffect, useMemo, useState } from "react";

import { useStreams } from "@/hooks/useIngest";
import { useUI, type ToolId } from "@/store/ui";

interface Command {
  id: string;
  label: string;
  hint: string;
  run: () => void;
}

export function CommandPalette() {
  const open = useUI((s) => s.paletteOpen);
  const setPalette = useUI((s) => s.setPalette);
  const setTool = useUI((s) => s.setTool);
  const select = useUI((s) => s.select);
  const { data: streams } = useStreams();
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);

  const commands = useMemo<Command[]>(() => {
    const tools: [ToolId, string][] = [
      ["wall", "Go to Video Wall"],
      ["map", "Go to Site Map"],
      ["incidents", "Go to Incident Queue"],
      ["cameras", "Go to Cameras"],
      ["signatures", "Go to Signatures"],
      ["health", "Go to Health"],
    ];
    const base: Command[] = tools.map(([id, label]) => ({
      id: `tool:${id}`,
      label,
      hint: "view",
      run: () => setTool(id),
    }));
    const cams: Command[] = (streams ?? []).map((s) => ({
      id: `cam:${s.camera_id}`,
      label: s.name,
      hint: "camera",
      run: () => {
        setTool("wall");
        select({ kind: "camera", id: s.camera_id, label: s.name, data: { ...s.health } });
      },
    }));
    return [...base, ...cams];
  }, [streams, setTool, select]);

  const filtered = useMemo(() => {
    const needle = q.toLowerCase();
    return commands.filter((c) => c.label.toLowerCase().includes(needle)).slice(0, 12);
  }, [commands, q]);

  useEffect(() => {
    if (!open) {
      setQ("");
      setIdx(0);
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/50 pt-[12vh]"
      onMouseDown={() => setPalette(false)}
    >
      <div
        className="w-[560px] max-w-[92vw] border border-line bg-raised shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          autoFocus
          value={q}
          placeholder="Jump to a camera, view, or action..."
          onChange={(e) => {
            setQ(e.target.value);
            setIdx(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setIdx((i) => Math.min(i + 1, filtered.length - 1));
            if (e.key === "ArrowUp") setIdx((i) => Math.max(i - 1, 0));
            if (e.key === "Escape") setPalette(false);
            if (e.key === "Enter" && filtered[idx]) {
              filtered[idx].run();
              setPalette(false);
            }
          }}
          className="w-full bg-transparent px-4 py-3 text-[14px] text-fg outline-none placeholder:text-fg-muted"
        />
        <div className="max-h-[50vh] overflow-auto border-t border-line">
          {filtered.length === 0 ? (
            <div className="px-4 py-3 text-[12px] text-fg-muted">No matches</div>
          ) : (
            filtered.map((c, i) => (
              <button
                key={c.id}
                className={`flex w-full items-center justify-between px-4 py-2 text-left ${
                  i === idx ? "bg-panel text-fg" : "text-fg-secondary hover:bg-panel"
                }`}
                onMouseEnter={() => setIdx(i)}
                onMouseDown={() => {
                  c.run();
                  setPalette(false);
                }}
              >
                <span className="text-[13px]">{c.label}</span>
                <span className="mono text-[10px] uppercase text-fg-muted">{c.hint}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
