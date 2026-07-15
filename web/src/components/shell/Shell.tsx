"use client";

import { useEffect } from "react";

import { useAuth } from "@/store/auth";
import { useUI } from "@/store/ui";

import { CommandPalette } from "./CommandPalette";
import { EditorArea } from "./EditorArea";
import { InspectorDock } from "./InspectorDock";
import { MenuBar } from "./MenuBar";
import { StatusBar } from "./StatusBar";
import { ToolRail } from "./ToolRail";

export function Shell() {
  const inspectorOpen = useUI((s) => s.inspectorOpen);
  const setPalette = useUI((s) => s.setPalette);

  useEffect(() => {
    useAuth.getState().init();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette]);

  return (
    <div className="grid h-screen grid-rows-[28px_1fr_24px] overflow-hidden bg-base text-fg">
      <MenuBar />
      <div
        className="grid overflow-hidden"
        style={{
          gridTemplateColumns: `44px minmax(0,1fr) ${inspectorOpen ? "320px" : "0px"}`,
        }}
      >
        <ToolRail />
        <main className="min-w-0 overflow-hidden border-l border-line bg-base">
          <EditorArea />
        </main>
        <InspectorDock />
      </div>
      <StatusBar />
      <CommandPalette />
    </div>
  );
}
