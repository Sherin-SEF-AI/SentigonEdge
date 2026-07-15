"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Car, Link2, MapPin, User } from "lucide-react";
import { useState } from "react";

import { crosssite, type CrossSiteLink, type SiteOverview } from "@/lib/api";

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

function SiteCard({ s }: { s: SiteOverview }) {
  const allOnline = s.online_cameras >= s.camera_count;
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <MapPin size={13} className="shrink-0 text-cyan" />
          <span className="truncate text-[13px] text-fg">{s.name}</span>
        </div>
        {s.open_incidents > 0 && (
          <span className="mono shrink-0 rounded-[2px] bg-red/20 px-1.5 py-0.5 text-[9px] uppercase text-red">
            {s.open_incidents} open
          </span>
        )}
      </div>
      <div className="mono mt-0.5 text-[10px] uppercase text-fg-muted">{s.timezone ?? "no tz"}</div>
      <div className="mt-2 flex items-center justify-between border-t border-line pt-2 text-[11px]">
        <span className="text-fg-muted">cameras</span>
        <span className={`mono ${allOnline ? "text-green" : "text-amber"}`}>
          {s.online_cameras}/{s.camera_count}
        </span>
      </div>
      {s.cross_site_links != null && (
        <div className="mt-1 flex items-center justify-between text-[11px]">
          <span className="text-fg-muted">links</span>
          <span className="mono text-magenta">{s.cross_site_links}</span>
        </div>
      )}
    </div>
  );
}

function AddSiteForm() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const m = useMutation({
    mutationFn: () => crosssite.createSite({ name, timezone: timezone || undefined }),
    onSuccess: (res: unknown) => {
      const r = res as { id?: string; detail?: string } | null;
      if (r && r.id) {
        setName("");
        setTimezone("");
        setMsg({ ok: true, text: "site created" });
        qc.invalidateQueries({ queryKey: ["crosssite-overview"] });
        qc.invalidateQueries({ queryKey: ["crosssite-sites"] });
      } else {
        setMsg({ ok: false, text: "admin required" });
      }
    },
    onError: () => setMsg({ ok: false, text: "admin required" }),
  });
  return (
    <div className="flex flex-wrap items-end gap-2 border border-line bg-panel p-3">
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">site name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none"
        />
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase text-fg-muted">timezone</label>
        <input
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          placeholder="e.g. America/New_York"
          className="rounded-[3px] border border-line bg-base px-2 py-1 text-[12px] text-fg outline-none"
        />
      </div>
      <button
        disabled={!name || m.isPending}
        onClick={() => {
          setMsg(null);
          m.mutate();
        }}
        className="mono rounded-[3px] bg-raised px-3 py-1.5 text-[11px] uppercase text-cyan hover:bg-base disabled:opacity-40"
      >
        {m.isPending ? "adding…" : "add site"}
      </button>
      {msg && <span className={`mono text-[11px] ${msg.ok ? "text-green" : "text-amber"}`}>{msg.text}</span>}
    </div>
  );
}

function LinkCard({ l }: { l: CrossSiteLink }) {
  const isPlate = l.entity_type === "plate";
  const Icon = isPlate ? Car : User;
  return (
    <div className="border border-line bg-panel p-3">
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1 rounded-[2px] bg-magenta/15 px-1.5 py-0.5 text-magenta">
          <Icon size={12} />
          <span className="mono text-[9px] uppercase">{l.entity_type}</span>
        </span>
        <span className="truncate text-[12px] text-fg">{l.label ?? l.entity_key}</span>
        <span className="mono ml-auto text-[10px] text-fg-muted">{timeAgo(l.last_seen_at)}</span>
      </div>
      <div className="mono mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-fg-muted">
        <span className="flex items-center gap-1">
          <Link2 size={11} className="text-cyan" />
          {l.site_count} sites
        </span>
        <span>{l.sighting_count} sightings</span>
        {l.score != null && <span className="text-cyan">score {l.score.toFixed(2)}</span>}
        <span>first {timeAgo(l.first_seen_at)}</span>
      </div>
      {(l.sites ?? []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {(l.sites ?? []).map((s, i) => (
            <span key={i} className="mono rounded-[2px] bg-raised px-1.5 py-0.5 text-[9px] text-fg-secondary">
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function CrossSiteScreen() {
  const overviewQ = useQuery({
    queryKey: ["crosssite-overview"],
    queryFn: ({ signal }) => crosssite.overview(signal),
    refetchInterval: 5000,
  });
  const linksQ = useQuery({
    queryKey: ["crosssite-links"],
    queryFn: ({ signal }) => crosssite.links(signal),
    refetchInterval: 5000,
  });

  const sites: SiteOverview[] = overviewQ.data ?? [];
  const links: CrossSiteLink[] = linksQ.data ?? [];

  return (
    <div className="flex h-full flex-col overflow-auto p-4">
      <div className="mb-3">
        <div className="text-[13px] font-semibold text-fg">Multi-Site &amp; Cross-Site</div>
        <div className="text-[11px] text-fg-muted">Site rollup and entities correlated across sites.</div>
      </div>

      <div className="mb-3">
        <AddSiteForm />
      </div>

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Sites</div>
      {sites.length === 0 ? (
        <div className="mono mb-6 text-[12px] text-fg-muted">no sites provisioned yet</div>
      ) : (
        <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {sites.map((s) => (
            <SiteCard key={s.id} s={s} />
          ))}
        </div>
      )}

      <div className="mb-2 text-[11px] uppercase tracking-[0.15em] text-fg-secondary">Cross-site correlations</div>
      {links.length === 0 ? (
        <div className="mono max-w-2xl border border-line bg-panel p-4 text-[12px] leading-relaxed text-fg-muted">
          No cross-site correlations yet — links appear automatically when the same vehicle (plate) or person
          (appearance) is seen at 2+ sites. Provision a second site and onboard its cameras to activate.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-3">
          {links.map((l) => (
            <LinkCard key={l.id} l={l} />
          ))}
        </div>
      )}
    </div>
  );
}
