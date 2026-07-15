"use client";

import { useEffect, useRef, useState } from "react";

import { AuthBar } from "@/components/auth/AuthBar";
import { useUI, type ToolId } from "@/store/ui";

interface MenuItem {
  label: string;
  accel?: string;
  tool?: ToolId;
  action?: () => void;
}
interface Menu {
  label: string;
  items: MenuItem[];
}

function useMenus(): Menu[] {
  const setTool = useUI((s) => s.setTool);
  const setPalette = useUI((s) => s.setPalette);
  const toggleInspector = useUI((s) => s.toggleInspector);
  return [
    {
      label: "File",
      items: [
        { label: "Command Palette", accel: "Ctrl K", action: () => setPalette(true) },
        { label: "Export Layout", accel: "" },
      ],
    },
    {
      label: "Edit",
      items: [{ label: "Preferences" }, { label: "Ontology" }],
    },
    {
      label: "View",
      items: [
        { label: "Toggle Inspector", accel: "N", action: toggleInspector },
        { label: "Video Wall", tool: "wall" },
        { label: "Single Camera", tool: "live" },
        { label: "Site Map", tool: "map" },
        { label: "Context Graph", tool: "graph" },
      ],
    },
    {
      label: "Cameras",
      items: [
        { label: "Camera Grid", tool: "cameras" },
        { label: "ONVIF Discovery" },
      ],
    },
    {
      label: "Signatures",
      items: [{ label: "Signature Library", tool: "signatures" }],
    },
    {
      label: "Investigate",
      items: [
        { label: "Threat Queue", tool: "threats" },
        { label: "Incident Queue", tool: "incidents" },
        { label: "Shift Handover", tool: "handover" },
        { label: "Cases", tool: "cases" },
        { label: "Semantic Search", tool: "search" },
        { label: "Entity Trajectories", tool: "trajectory" },
        { label: "Incident Reconstruction", tool: "reconstruction" },
      ],
    },
    {
      label: "Analytics",
      items: [
        { label: "Behavioral Anomalies", tool: "anomaly" },
        { label: "Signals Fusion", tool: "fusion" },
        { label: "Dashboards", tool: "analytics" },
        { label: "Model Governance", tool: "models" },
        { label: "Health", tool: "health" },
      ],
    },
    {
      label: "Window",
      items: [
        { label: "Administration", tool: "admin" },
        { label: "Reset Layout" },
      ],
    },
    { label: "Help", items: [{ label: "About Sentigon" }] },
  ];
}

export function MenuBar() {
  const menus = useMenus();
  const setTool = useUI((s) => s.setTool);
  const [open, setOpen] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(null);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, []);

  return (
    <div
      ref={ref}
      className="flex select-none items-stretch bg-panel px-1 text-[12px] text-fg-secondary"
    >
      <span className="flex items-center gap-1.5 px-2 font-semibold tracking-wide text-fg">
        <span className="inline-block h-2.5 w-2.5 rounded-[2px] bg-cyan" />
        SENTIGON
      </span>
      {menus.map((menu) => (
        <div key={menu.label} className="relative">
          <button
            className={`h-full px-2.5 hover:bg-raised hover:text-fg ${
              open === menu.label ? "bg-raised text-fg" : ""
            }`}
            onMouseDown={(e) => {
              e.stopPropagation();
              setOpen((o) => (o === menu.label ? null : menu.label));
            }}
            onMouseEnter={() => open && setOpen(menu.label)}
          >
            {menu.label}
          </button>
          {open === menu.label && (
            <div className="absolute left-0 top-full z-50 min-w-[220px] border border-line bg-raised py-1 shadow-lg">
              {menu.items.map((item) => (
                <button
                  key={item.label}
                  className="flex w-full items-center justify-between px-3 py-1.5 text-left text-fg-secondary hover:bg-panel hover:text-fg"
                  onMouseDown={(e) => {
                    e.stopPropagation();
                    if (item.tool) setTool(item.tool);
                    item.action?.();
                    setOpen(null);
                  }}
                >
                  <span>{item.label}</span>
                  {item.accel ? (
                    <span className="mono ml-6 text-[11px] text-fg-muted">{item.accel}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
      <AuthBar />
    </div>
  );
}
