// Service origins. Ingest owns cameras/streams/health in Phase 1; the core API
// (incidents, search, cases) comes online in later phases.
export const INGEST_URL = process.env.NEXT_PUBLIC_INGEST_URL ?? "http://localhost:8020";
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010";
export const PERCEPTION_URL = process.env.NEXT_PUBLIC_PERCEPTION_URL ?? "http://localhost:8030";
export const SEARCH_URL = process.env.NEXT_PUBLIC_SEARCH_URL ?? "http://localhost:8060";

export interface SearchResult {
  incident_id: string;
  title: string;
  signature: string | null;
  camera: string | null;
  severity: string;
  ts: string;
  score: number;
  snapshot_url: string | null;
}

export interface StreamHealth {
  status: "online" | "offline" | "connecting" | string;
  fps: number;
  jitter_ms: number;
  decode_errors: number;
  reconnects: number;
  frames_total: number;
  resolution: string | null;
}

export interface Stream {
  camera_id: string;
  name: string;
  rtsp_uri: string;
  whep_url: string;
  hls_url: string;
  health: StreamHealth;
}

export interface IngestSummary {
  cameras: number;
  online: number;
  aggregate_fps: number;
  mediamtx: string;
}

let _authToken: string | null = null;
export function setAuthToken(t: string | null): void {
  _authToken = t;
}
export function authHeaders(): Record<string, string> {
  return _authToken ? { Authorization: `Bearer ${_authToken}` } : {};
}

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  // Reads are now authenticated server-side, so send the bearer token like writes do.
  const res = await fetch(url, { signal, cache: "no-store", headers: { ...authHeaders() } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

async function postJSON<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface AuthUser {
  email: string;
  name: string;
  role: string;
  access_token: string;
}

export function login(email: string, password: string): Promise<AuthUser> {
  return postJSON<AuthUser>(`${API_URL}/auth/login`, { email, password });
}

export function authMe(token: string): Promise<{ email: string; name: string; role: string }> {
  return fetch(`${API_URL}/auth/me`, { headers: { Authorization: `Bearer ${token}` } }).then((r) => {
    if (!r.ok) throw new Error("unauthorized");
    return r.json();
  });
}

export interface Incident {
  id: string;
  seq: number;
  title: string;
  severity: string;
  status: string;
  verdict: string | null;
  confidence: number;
  risk_score: number | null;
  priority: string | null;
  occurrence_count?: number;
  signature: string | null;
  camera_id: string;
  camera: string | null;
  zone_id: string | null;
  snapshot_url: string | null;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface Summary {
  open_incidents: number;
  total_incidents: number;
  by_severity: Record<string, number>;
}

export interface Sig {
  id: string;
  name: string;
  category: string;
  description: string | null;
  severity: string;
  detection_method: string;
  enabled: boolean;
  source: string;
  detection_count: number;
  params: Record<string, unknown> | null;
}

export interface ZoneRow {
  id: string;
  name: string;
  zone_type: string;
  camera_id: string | null;
  polygon: number[][] | null;
  max_occupancy: number | null;
}

export const core = {
  incidents: (status?: string, signal?: AbortSignal) =>
    getJSON<Incident[]>(`${API_URL}/incidents?limit=150${status ? `&status=${status}` : ""}`, signal),
  incident: (id: string, signal?: AbortSignal) =>
    getJSON<Record<string, unknown>>(`${API_URL}/incidents/${id}`, signal),
  action: (id: string, action: string, note = "") =>
    postJSON(`${API_URL}/incidents/${id}/${action}`, { note }),
  bulkAction: (ids: string[], action: string, note = "") =>
    postJSON<{ updated: number; action: string }>(`${API_URL}/incidents/bulk`, { ids, action, note }),
  shiftHandover: (hours = 8, signal?: AbortSignal) =>
    getJSON<ShiftHandover>(`${API_URL}/shift-handover?hours=${hours}`, signal),
  summary: (signal?: AbortSignal) => getJSON<Summary>(`${API_URL}/summary`, signal),
  signatures: (signal?: AbortSignal) => getJSON<Sig[]>(`${API_URL}/signatures?limit=500`, signal),
  patchSignature: (id: string, body: Record<string, unknown>) =>
    fetch(`${API_URL}/signatures/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }),
  createOpenVocab: (body: { name: string; prompt: string; severity: string }) =>
    postJSON(`${API_URL}/signatures/open-vocab`, body),
  zones: (cameraId?: string, signal?: AbortSignal) =>
    getJSON<ZoneRow[]>(`${API_URL}/zones${cameraId ? `?camera_id=${cameraId}` : ""}`, signal),
  createZone: (body: {
    name: string;
    zone_type: string;
    camera_id: string;
    polygon: number[][];
    max_occupancy?: number | null;
  }) => postJSON<{ id: string }>(`${API_URL}/zones`, body),
  cameras: (signal?: AbortSignal) =>
    getJSON<{ id: string; name: string; rtsp_uri: string; status: string }[]>(
      `${API_URL}/cameras`,
      signal,
    ),
  renameCamera: (id: string, name: string) =>
    fetch(`${API_URL}/cameras/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ name }),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.json();
    }),
  deleteCamera: (id: string) =>
    fetch(`${API_URL}/cameras/${id}`, { method: "DELETE", headers: { ...authHeaders() } }).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      return r.json() as Promise<{ deleted: string; name: string; objects_removed: number }>;
    }),
  cases: (signal?: AbortSignal) =>
    getJSON<{ id: string; title: string; status: string; priority: string; incidents: number; created_at: string }[]>(
      `${API_URL}/cases`,
      signal,
    ),
  caseDetail: (id: string, signal?: AbortSignal) =>
    getJSON<Record<string, unknown>>(`${API_URL}/cases/${id}`, signal),
  caseExport: (id: string) => getJSON<Record<string, unknown>>(`${API_URL}/cases/${id}/export`),
  analyticsOverview: (signal?: AbortSignal) =>
    getJSON<{
      total_incidents: number;
      by_severity: Record<string, number>;
      by_status: Record<string, number>;
      verified: number;
      confirmed: number;
      rejected: number;
      false_alarm_rate: number;
    }>(`${API_URL}/analytics/overview`, signal),
  analyticsTimeseries: (signal?: AbortSignal) =>
    getJSON<{ t: string; critical: number; high: number; medium: number; low: number }[]>(
      `${API_URL}/analytics/timeseries?hours=3`,
      signal,
    ),
  analyticsBySignature: (signal?: AbortSignal) =>
    getJSON<{ signature: string; count: number }[]>(`${API_URL}/analytics/by-signature?limit=10`, signal),
  analyticsByCamera: (signal?: AbortSignal) =>
    getJSON<{ camera: string; count: number }[]>(`${API_URL}/analytics/by-camera`, signal),
  models: (signal?: AbortSignal) => getJSON<ModelVersion[]>(`${API_URL}/models`, signal),
  registerModels: () => postJSON<{ registered: number }>(`${API_URL}/models/register`),
  promoteModel: (id: string) => postJSON<{ promoted: string; role: string }>(`${API_URL}/models/${id}/promote`),
  camerasFull: (signal?: AbortSignal) =>
    getJSON<CameraFull[]>(`${API_URL}/cameras`, signal),
  setCameraMap: (id: string, body: { lat: number; lng: number; heading: number; fov: number }) =>
    fetch(`${API_URL}/cameras/${id}/map`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }),
  graph: (signal?: AbortSignal) => getJSON<GraphData>(`${API_URL}/graph`, signal),
  healthServices: (signal?: AbortSignal) => getJSON<HealthReport>(`${API_URL}/health/services`, signal),
  users: (token: string, signal?: AbortSignal) =>
    fetch(`${API_URL}/users`, { headers: { Authorization: `Bearer ${token}` }, signal }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json() as Promise<AdminUser[]>;
    }),
  createUser: (body: { email: string; full_name: string; password: string; role: string }) =>
    postJSON<AdminUser>(`${API_URL}/users`, body),
  patchUser: (id: string, body: { role?: string; is_active?: boolean }) =>
    fetch(`${API_URL}/users/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }),
  audit: (token: string, limit = 100, signal?: AbortSignal) =>
    fetch(`${API_URL}/audit?limit=${limit}`, { headers: { Authorization: `Bearer ${token}` }, signal }).then(
      (r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json() as Promise<AuditEntry[]>;
      },
    ),
};

export interface CameraFull {
  id: string;
  name: string;
  rtsp_uri: string;
  status: string;
  map: { lat: number; lng: number; heading: number; fov: number } | null;
}
export interface GraphNode {
  id: string;
  kind: "camera" | "zone" | "signature" | "incident";
  label: string;
  severity?: string;
  status?: string;
}
export interface GraphEdge {
  source: string;
  target: string;
  rel: string;
}
export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
export interface ShiftHandover {
  shift_hours: number;
  incidents_this_shift: number;
  open_by_severity: Record<string, number>;
  unacknowledged: number;
  escalations_this_shift: number;
  cameras: { name: string; status: string }[];
  top_open_incidents: { id: string; title: string; severity: string; status: string; created_at: string | null }[];
}
export interface ServiceHealth {
  name: string;
  up: boolean;
  stats: Record<string, unknown> | null;
}
export interface PerceptionCam {
  camera_id: string;
  name: string;
  status: string;
  fps: number;
  objects: number;
  inference_ms: number;
}
export interface HealthReport {
  services: ServiceHealth[];
  cameras: PerceptionCam[];
}
export interface AdminUser {
  id: string;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
}
export interface AuditEntry {
  id: string;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  details: Record<string, unknown> | null;
  ts: string | null;
}

export interface ModelVersion {
  id: string;
  name: string;
  role: string;
  version: string;
  stage: string;
  artifact_ref: string | null;
  params: Record<string, unknown> | null;
  promoted_at: string | null;
  latest_eval: Record<string, unknown> | null;
}

export function ingestSnapshot(cameraId: string): Promise<{ url: string }> {
  return fetch(`${INGEST_URL}/cameras/${cameraId}/snapshot`, { method: "POST" }).then((r) => r.json());
}

export function snapshotUrl(cameraId: string): string {
  return `${INGEST_URL}/cameras/${cameraId}/snapshot`;
}

export function incidentSnapshotUrl(
  incidentId: string,
  opts?: { blur?: boolean; faces?: boolean },
): string {
  const q: string[] = [];
  if (opts?.blur) q.push("blur=1");
  if (opts?.faces) q.push("faces=1");
  return `${API_URL}/incidents/${incidentId}/snapshot${q.length ? `?${q.join("&")}` : ""}`;
}

export interface ReconEntry {
  type: "incident" | "appearance" | "related_incident";
  kind: string;
  ts: string | null;
  camera_id?: string;
  camera?: string;
  signature?: string | null;
  severity?: string;
  title?: string;
  incident_id?: string;
  match_score?: number;
  snapshot?: boolean;
}

export interface Reconstruction {
  incident_id: string;
  anchor_ts: string;
  window_s: number;
  subject_track: number | null;
  involved_cameras: string[];
  trajectory_found: boolean;
  timeline: ReconEntry[];
  recording_segments: {
    camera_id: string;
    camera: string;
    start: string;
    end: string | null;
    object_key: string;
    bucket: string;
  }[];
  counts: {
    timeline_entries: number;
    cross_camera_appearances: number;
    related_incidents: number;
    recording_segments: number;
  };
}

export function incidentReconstruction(
  incidentId: string,
  windowS = 180,
  signal?: AbortSignal,
): Promise<Reconstruction> {
  return getJSON<Reconstruction>(
    `${API_URL}/incidents/${incidentId}/reconstruction?window_s=${windowS}`,
    signal,
  );
}

export interface Threat {
  id: string;
  title: string;
  signature: string | null;
  severity: string;
  status: string;
  verdict: string | null;
  camera: string | null;
  risk_score: number;
  priority: string;
  corroborating_signals: number;
  occurrence_count: number;
  score_breakdown: Record<string, number>;
  created_at: string;
}

export function threatQueue(minScore = 0, signal?: AbortSignal): Promise<{ count: number; threats: Threat[] }> {
  return getJSON(`${API_URL}/threats?limit=40&min_score=${minScore}`, signal);
}

export interface ZoneBaseline {
  zone_id: string;
  zone: string;
  zone_type: string | null;
  baseline_mean: number;
  baseline_std: number;
  samples: number;
  current_occupancy: number | null;
  z_score: number | null;
  anomalous: boolean;
  learned: boolean;
}

export function zoneBaselines(signal?: AbortSignal): Promise<{ zones: ZoneBaseline[] }> {
  return getJSON(`${API_URL}/analytics/baselines`, signal);
}

export interface PlaybookActivity {
  ts: string;
  incident_id: string;
  playbook: string;
  actions: string[];
}

export function playbookActivity(
  limit = 30,
  signal?: AbortSignal,
): Promise<{ count: number; activity: PlaybookActivity[] }> {
  return getJSON(`${API_URL}/playbooks/activity?limit=${limit}`, signal);
}

export interface AccessSignal {
  id: string;
  ts: string;
  event_type: string;
  threatening: boolean;
  door_id: string | null;
  badge_id: string | null;
  camera: string | null;
  bound_incident: string | null;
}

export interface VideoSignal {
  id: string;
  ts: string;
  signature: string | null;
  severity: string;
  status: string;
  risk_score: number | null;
  camera: string | null;
  fused: boolean;
}

export interface FusionTimeline {
  window_minutes: number;
  access_events: AccessSignal[];
  video_incidents: VideoSignal[];
  fusions: { access_event_id: string; incident_id: string; ts: string; event_type: string; camera: string | null }[];
  counts: { access: number; video: number; fused: number };
}

export function fusionTimeline(minutes = 30, signal?: AbortSignal): Promise<FusionTimeline> {
  return getJSON(`${API_URL}/fusion/timeline?minutes=${minutes}`, signal);
}

export interface WallPriority {
  camera_id: string;
  camera: string;
  score: number;
  open_incidents: number;
  max_risk: number;
  live_objects: number;
  rank: number;
}

export function wallPriority(signal?: AbortSignal): Promise<{ cameras: WallPriority[] }> {
  return getJSON(`${API_URL}/wall/priority`, signal);
}

export interface ReidTrack {
  camera_id: string;
  camera: string;
  track_id: number;
  object_class: string;
  appearances: number;
  first_ts: string;
  last_ts: string;
}

export interface TrajectoryMatch {
  camera_id: string;
  camera: string;
  track_id: number;
  object_class: string;
  match_score: number;
  matched_ts: string | null;
  hits: number;
}

export interface TrajectoryEvent {
  camera_id: string;
  camera: string;
  track_id: number;
  kind: "origin" | "match";
  ts: string | null;
  last_ts?: string | null;
  appearances?: number;
  object_class?: string;
  match_score: number;
}

export interface Trajectory {
  found: boolean;
  reason?: string;
  query?: {
    camera_id: string;
    camera: string;
    track_id: number;
    object_class: string;
    appearances: number;
    continuity_cohesion: number;
    first_ts: string | null;
    last_ts: string | null;
  };
  cross_camera_matches?: TrajectoryMatch[];
  timeline?: TrajectoryEvent[];
}

export const reid = {
  tracks: (limit = 40, signal?: AbortSignal) =>
    getJSON<{ count: number; tracks: ReidTrack[] }>(`${SEARCH_URL}/reid/tracks?limit=${limit}`, signal),
  trajectory: (cameraId: string, trackId: number, minScore = 0.5, signal?: AbortSignal) =>
    getJSON<Trajectory>(
      `${SEARCH_URL}/reid/trajectory?camera_id=${cameraId}&track_id=${trackId}&min_score=${minScore}`,
      signal,
    ),
};

export async function searchEvidence(
  q: string,
  signal?: AbortSignal,
): Promise<{ query: string; count: number; results: SearchResult[] }> {
  return getJSON(`${SEARCH_URL}/search?q=${encodeURIComponent(q)}&limit=24`, signal);
}

export const ingest = {
  streams: (signal?: AbortSignal) => getJSON<Stream[]>(`${INGEST_URL}/streams`, signal),
  summary: (signal?: AbortSignal) => getJSON<IngestSummary>(`${INGEST_URL}/health/summary`, signal),
  cameras: (signal?: AbortSignal) => getJSON<unknown[]>(`${INGEST_URL}/cameras`, signal),
  readyz: (signal?: AbortSignal) => getJSON<{ status: string }>(`${INGEST_URL}/readyz`, signal),
};

// ── Media Source (8055): USB / v4l2 camera scan + onboard ────────────────────
export const MEDIASOURCE_URL = process.env.NEXT_PUBLIC_MEDIASOURCE_URL ?? "http://localhost:8055";

export interface UsbMode {
  format: string;
  resolution: string;
}
export interface UsbDevice {
  device: string;
  index: number;
  name: string;
  capture: boolean;
  modes: UsbMode[];
  suggested: UsbMode;
  registered: boolean;
}
export interface UsbAddResult {
  name: string;
  path: string;
  status: string;
  camera_id: string | null;
}

export const mediasource = {
  scanUsb: (signal?: AbortSignal) => getJSON<UsbDevice[]>(`${MEDIASOURCE_URL}/usb/scan`, signal),
  addUsb: (body: {
    device: string;
    name: string;
    fps?: number;
    resolution?: string;
    input_format?: string;
    zone_name?: string;
  }) => postJSON<UsbAddResult>(`${MEDIASOURCE_URL}/usb/add`, body),
};

// ── SOC Dispatch (8081), Fleet Health (8082), Cross-Site (8086) ──────────────
export const DISPATCH_URL = process.env.NEXT_PUBLIC_DISPATCH_URL ?? "http://localhost:8081";
export const FLEET_URL = process.env.NEXT_PUBLIC_FLEET_URL ?? "http://localhost:8082";
export const CROSSSITE_URL = process.env.NEXT_PUBLIC_CROSSSITE_URL ?? "http://localhost:8086";

export interface Dispatch {
  id: string;
  incident_id: string;
  camera_id: string | null;
  site_id: string | null;
  responder_id: string | null;
  responder_name: string | null;
  severity: string;
  risk_score: number | null;
  signature_name: string | null;
  sitrep: string | null;
  state: string;
  tier: number;
  notified_at: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  ack_by: string | null;
  created_at: string;
  sla_ack_seconds: number;
}

export interface Responder {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  role: string;
  channels: string[];
  site_id: string | null;
  active: boolean;
}

export interface Shift {
  id: string;
  operator: string;
  started_at: string;
  active: boolean;
}

export const dispatch = {
  stats: (signal?: AbortSignal) => getJSON<Record<string, number>>(`${DISPATCH_URL}/stats`, signal),
  list: (state?: string, signal?: AbortSignal) =>
    getJSON<Dispatch[]>(`${DISPATCH_URL}/dispatches${state ? `?state=${state}` : ""}`, signal),
  ack: (id: string, by?: string) =>
    fetch(`${DISPATCH_URL}/dispatches/${id}/ack`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ by }),
    }).then((r) => r.json()),
  resolve: (id: string, by?: string, notes?: string) =>
    fetch(`${DISPATCH_URL}/dispatches/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ by, notes }),
    }).then((r) => r.json()),
  responders: (signal?: AbortSignal) => getJSON<Responder[]>(`${DISPATCH_URL}/responders`, signal),
  activeShifts: (signal?: AbortSignal) => getJSON<Shift[]>(`${DISPATCH_URL}/shifts/active`, signal),
  checkin: (operator: string) =>
    fetch(`${DISPATCH_URL}/shifts/checkin`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ operator }),
    }).then((r) => r.json()),
  checkout: (operator: string) =>
    fetch(`${DISPATCH_URL}/shifts/checkout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ operator }),
    }).then((r) => r.json()),
};

export interface FleetOverview {
  cameras_total: number;
  cameras_online: number;
  services_total: number;
  services_up: number;
  findings_active: number;
  host: {
    disk_pct: number | null;
    mem_pct: number | null;
    gpu_pct: number | null;
    load1: number | null;
    cpu_count?: number;
  };
  severity?: Record<string, number>;
}

export interface FleetCameraHealth {
  status?: string;
  fps?: number;
  jitter_ms?: number;
  reconnects?: number;
  decode_errors?: number;
  frames_total?: number;
  resolution?: string | null;
}

export interface FleetCamera {
  id: string;
  name: string;
  site_id: string | null;
  status: string;
  health: FleetCameraHealth | null;
  last_seen: string | null;
  target_fps: number | null;
}

export interface FleetService {
  name: string;
  up: boolean;
  detail: string;
  stats: Record<string, unknown> | null;
  latency_ms: number;
}

export interface FleetFinding {
  id: string;
  kind: string;
  severity: string;
  target_type: string;
  target_id: string | null;
  target_name: string | null;
  detail: string | null;
  metric: number | null;
  recommended_action: string | null;
  active: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export const fleet = {
  overview: (signal?: AbortSignal) => getJSON<FleetOverview>(`${FLEET_URL}/fleet/overview`, signal),
  cameras: (signal?: AbortSignal) => getJSON<FleetCamera[]>(`${FLEET_URL}/fleet/cameras`, signal),
  services: (signal?: AbortSignal) => getJSON<FleetService[]>(`${FLEET_URL}/fleet/services`, signal),
  findings: (signal?: AbortSignal) => getJSON<FleetFinding[]>(`${FLEET_URL}/fleet/findings?active=true`, signal),
};

export interface SiteOverview {
  id: string;
  name: string;
  timezone: string | null;
  camera_count: number;
  online_cameras: number;
  open_incidents: number;
  cross_site_links?: number;
}

export interface Site {
  id: string;
  name: string;
  address: string | null;
  timezone: string | null;
  center: unknown;
  meta: Record<string, unknown> | null;
  created_at: string | null;
  camera_count: number;
  online_cameras: number;
}

export interface CrossSiteLink {
  id: string;
  entity_type: string;
  entity_key: string;
  label: string | null;
  sites: string[];
  site_count: number;
  sighting_count: number;
  cameras: string[];
  score: number | null;
  active: boolean;
  first_seen_at: string | null;
  last_seen_at: string | null;
}

export const crosssite = {
  overview: (signal?: AbortSignal) => getJSON<SiteOverview[]>(`${CROSSSITE_URL}/overview`, signal),
  sites: (signal?: AbortSignal) => getJSON<Site[]>(`${CROSSSITE_URL}/sites`, signal),
  links: (signal?: AbortSignal) => getJSON<CrossSiteLink[]>(`${CROSSSITE_URL}/crosssite/links?active=true`, signal),
  createSite: (body: { name: string; address?: string; timezone?: string }) =>
    fetch(`${CROSSSITE_URL}/sites`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
};
