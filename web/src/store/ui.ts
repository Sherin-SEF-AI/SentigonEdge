import { create } from "zustand";

export type ToolId =
  | "live"
  | "wall"
  | "map"
  | "graph"
  | "search"
  | "incidents"
  | "cases"
  | "cameras"
  | "signatures"
  | "analytics"
  | "health"
  | "models"
  | "trajectory"
  | "reconstruction"
  | "threats"
  | "anomaly"
  | "fusion"
  | "handover"
  | "dispatch"
  | "fleet"
  | "crosssite"
  | "admin";

export interface Selection {
  kind: "camera" | "incident" | "zone" | "object" | null;
  id: string | null;
  label?: string;
  data?: Record<string, unknown>;
}

interface UIState {
  tool: ToolId;
  setTool: (t: ToolId) => void;
  inspectorOpen: boolean;
  toggleInspector: () => void;
  selection: Selection;
  select: (s: Selection) => void;
  clearSelection: () => void;
  paletteOpen: boolean;
  setPalette: (v: boolean) => void;
}

export const useUI = create<UIState>((set) => ({
  tool: "wall",
  setTool: (tool) => set({ tool }),
  inspectorOpen: true,
  toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
  selection: { kind: null, id: null },
  select: (selection) => set({ selection, inspectorOpen: true }),
  clearSelection: () => set({ selection: { kind: null, id: null } }),
  paletteOpen: false,
  setPalette: (paletteOpen) => set({ paletteOpen }),
}));
