"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";

import { searchEvidence } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useUI } from "@/store/ui";

const SEV_DOT: Record<string, string> = {
  critical: "bg-red",
  high: "bg-amber",
  medium: "bg-cyan",
  low: "bg-fg-muted",
};

const EXAMPLES = [
  "a person walking in a retail store aisle",
  "people together at an entrance doorway",
  "an industrial warehouse floor with machinery",
];

export function SearchScreen() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const select = useUI((s) => s.select);

  const { data, isFetching } = useQuery({
    queryKey: ["search", query],
    queryFn: ({ signal }) => searchEvidence(query, signal),
    enabled: query.length > 0,
  });
  const results = data?.results ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line bg-panel px-3 py-2">
        <div className="flex items-center gap-2 rounded-[3px] border border-line bg-base px-2 py-1.5">
          <Search size={14} className="text-fg-muted" />
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setQuery(text.trim())}
            placeholder="Search all captured evidence in natural language..."
            className="w-full bg-transparent text-[13px] text-fg outline-none placeholder:text-fg-muted"
          />
          <button
            onClick={() => setQuery(text.trim())}
            className="mono rounded-[2px] bg-raised px-2 py-0.5 text-[11px] text-cyan hover:text-fg"
          >
            search
          </button>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setText(ex);
                setQuery(ex);
              }}
              className="rounded-[2px] border border-line-soft px-2 py-0.5 text-[11px] text-fg-muted hover:text-fg-secondary"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {!query ? (
          <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">
            enter a query to search real captured evidence (CLIP semantic search)
          </div>
        ) : isFetching && results.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">searching...</div>
        ) : results.length === 0 ? (
          <div className="flex h-full items-center justify-center text-[12px] text-fg-muted">no matches</div>
        ) : (
          <>
            <div className="mono mb-2 text-[11px] text-fg-muted">
              {results.length} results for &quot;{query}&quot;
            </div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-4">
              {results.map((r) => (
                <button
                  key={r.incident_id + r.score}
                  onClick={() =>
                    select({
                      kind: "incident",
                      id: r.incident_id,
                      label: r.title,
                      data: { camera: r.camera, signature: r.signature, severity: r.severity, score: r.score },
                    })
                  }
                  className="group overflow-hidden rounded-[3px] border border-line bg-panel text-left hover:border-cyan"
                >
                  <div className="relative aspect-video bg-black">
                    {r.snapshot_url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={r.snapshot_url} alt={r.title} className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-[10px] text-fg-muted">no image</div>
                    )}
                    <span className="mono absolute right-1 top-1 rounded-[2px] bg-black/70 px-1 text-[10px] text-cyan">
                      {r.score.toFixed(3)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 px-2 py-1.5">
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", SEV_DOT[r.severity])} />
                    <div className="min-w-0">
                      <div className="truncate text-[11px] text-fg">{r.title}</div>
                      <div className="mono truncate text-[10px] text-fg-muted">{r.camera}</div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
