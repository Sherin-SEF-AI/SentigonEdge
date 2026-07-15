"use client";

import {
  Activity,
  Boxes,
  Camera,
  Grid2x2,
  Map,
  Network,
  Search,
  Shield,
  ShieldAlert,
  Siren,
  SlidersHorizontal,
  Video,
} from "lucide-react";

import { useUI, type ToolId } from "@/store/ui";

const TOOLS: { id: ToolId; icon: React.ComponentType<{ size?: number }>; label: string }[] = [
  { id: "live", icon: Video, label: "Live" },
  { id: "wall", icon: Grid2x2, label: "Video Wall" },
  { id: "map", icon: Map, label: "Site Map" },
  { id: "graph", icon: Network, label: "Context Graph" },
  { id: "search", icon: Search, label: "Search" },
  { id: "incidents", icon: Siren, label: "Incidents" },
  { id: "cases", icon: Boxes, label: "Cases" },
  { id: "cameras", icon: Camera, label: "Cameras" },
  { id: "signatures", icon: ShieldAlert, label: "Signatures" },
  { id: "analytics", icon: Activity, label: "Analytics" },
  { id: "health", icon: SlidersHorizontal, label: "Health" },
  { id: "admin", icon: Shield, label: "Admin" },
];

export function ToolRail() {
  const tool = useUI((s) => s.tool);
  const setTool = useUI((s) => s.setTool);
  return (
    <nav className="flex flex-col items-center gap-0.5 bg-panel py-1.5">
      {TOOLS.map(({ id, icon: Icon, label }) => {
        const active = tool === id;
        return (
          <button
            key={id}
            title={label}
            onClick={() => setTool(id)}
            className={`group relative flex h-8 w-8 items-center justify-center rounded-[3px] ${
              active
                ? "bg-raised text-cyan"
                : "text-fg-muted hover:bg-raised hover:text-fg-secondary"
            }`}
          >
            {active && <span className="absolute left-0 h-4 w-[2px] rounded-full bg-cyan" />}
            <Icon size={17} />
          </button>
        );
      })}
    </nav>
  );
}
