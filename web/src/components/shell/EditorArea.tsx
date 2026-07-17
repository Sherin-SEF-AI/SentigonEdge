"use client";

import { AdminScreen } from "@/components/admin/AdminScreen";
import { AnalyticsScreen } from "@/components/analytics/AnalyticsScreen";
import { ZoneEditor } from "@/components/cameras/ZoneEditor";
import { CasesScreen } from "@/components/cases/CasesScreen";
import { DevicesScreen } from "@/components/devices/DevicesScreen";
import { CrossSiteScreen } from "@/components/crosssite/CrossSiteScreen";
import { DispatchScreen } from "@/components/dispatch/DispatchScreen";
import { FleetScreen } from "@/components/fleet/FleetScreen";
import { GraphScreen } from "@/components/graph/GraphScreen";
import { HandoverScreen } from "@/components/handover/HandoverScreen";
import { HealthScreen } from "@/components/health/HealthScreen";
import { IncidentQueue } from "@/components/incidents/IncidentQueue";
import { LiveScreen } from "@/components/live/LiveScreen";
import { MapScreen } from "@/components/map/MapScreen";
import { AnomalyScreen } from "@/components/anomaly/AnomalyScreen";
import { FusionScreen } from "@/components/fusion/FusionScreen";
import { ModelsScreen } from "@/components/models/ModelsScreen";
import { ReconstructionScreen } from "@/components/reconstruction/ReconstructionScreen";
import { ThreatQueueScreen } from "@/components/threats/ThreatQueueScreen";
import { SearchScreen } from "@/components/search/SearchScreen";
import { SignaturesScreen } from "@/components/signatures/SignaturesScreen";
import { TrajectoryScreen } from "@/components/trajectory/TrajectoryScreen";
import { VideoWall } from "@/components/wall/VideoWall";
import { useUI, type ToolId } from "@/store/ui";

const PHASE_NOTE: Partial<Record<ToolId, string>> = {};

function Placeholder({ tool }: { tool: ToolId }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
      <div className="mono text-[11px] uppercase tracking-[0.2em] text-fg-muted">{tool}</div>
      <div className="max-w-md text-[13px] text-fg-secondary">{PHASE_NOTE[tool]}</div>
    </div>
  );
}

export function EditorArea() {
  const tool = useUI((s) => s.tool);
  if (tool === "wall") return <VideoWall />;
  if (tool === "incidents") return <IncidentQueue />;
  if (tool === "search") return <SearchScreen />;
  if (tool === "signatures") return <SignaturesScreen />;
  if (tool === "cameras") return <ZoneEditor />;
  if (tool === "devices") return <DevicesScreen />;
  if (tool === "cases") return <CasesScreen />;
  if (tool === "analytics") return <AnalyticsScreen />;
  if (tool === "models") return <ModelsScreen />;
  if (tool === "trajectory") return <TrajectoryScreen />;
  if (tool === "reconstruction") return <ReconstructionScreen />;
  if (tool === "threats") return <ThreatQueueScreen />;
  if (tool === "anomaly") return <AnomalyScreen />;
  if (tool === "fusion") return <FusionScreen />;
  if (tool === "live") return <LiveScreen />;
  if (tool === "map") return <MapScreen />;
  if (tool === "graph") return <GraphScreen />;
  if (tool === "health") return <HealthScreen />;
  if (tool === "handover") return <HandoverScreen />;
  if (tool === "dispatch") return <DispatchScreen />;
  if (tool === "fleet") return <FleetScreen />;
  if (tool === "crosssite") return <CrossSiteScreen />;
  if (tool === "admin") return <AdminScreen />;
  return <Placeholder tool={tool} />;
}
