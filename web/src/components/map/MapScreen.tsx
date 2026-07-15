"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import { core, type CameraFull } from "@/lib/api";
import { useAuth } from "@/store/auth";

const DEFAULT_CENTER: [number, number] = [-122.4194, 37.7749]; // used only for the initial view
const STATUS_COLOR: Record<string, string> = { online: "#48C08A", offline: "#E5484D", degraded: "#F2A93B" };

// OSM raster style (no API key). The app is online; tiles load from OSM.
const OSM_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0E0F11" } },
    { id: "osm", type: "raster", source: "osm", paint: { "raster-brightness-max": 0.7, "raster-saturation": -0.4 } },
  ],
};

function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (c.getContext("webgl") || c.getContext("experimental-webgl"))
    );
  } catch {
    return false;
  }
}

function fovPolygon(lng: number, lat: number, heading: number, fov: number, meters = 45): number[][] {
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = meters / 111111;
  const dLng = meters / (111111 * Math.cos(toRad(lat)));
  const pts: number[][] = [[lng, lat]];
  for (let a = heading - fov / 2; a <= heading + fov / 2 + 0.001; a += fov / 12) {
    const r = toRad(a);
    pts.push([lng + dLng * Math.sin(r), lat + dLat * Math.cos(r)]);
  }
  pts.push([lng, lat]);
  return pts;
}

function coneFeatures(cams: CameraFull[]) {
  return {
    type: "FeatureCollection" as const,
    features: cams
      .filter((c) => c.map)
      .map((c) => ({
        type: "Feature" as const,
        properties: { status: c.status },
        geometry: {
          type: "Polygon" as const,
          coordinates: [fovPolygon(c.map!.lng, c.map!.lat, c.map!.heading, c.map!.fov)],
        },
      })),
  };
}

export function MapScreen() {
  const mapDiv = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const [cams, setCams] = useState<CameraFull[]>([]);
  const [placing, setPlacing] = useState<string | null>(null);
  const [glError, setGlError] = useState<string | null>(null);
  const placingRef = useRef<string | null>(null);
  const user = useAuth((s) => s.user);

  const load = async () => setCams(await core.camerasFull());

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (!mapDiv.current || mapRef.current || glError) return;
    if (!hasWebGL()) {
      setGlError("WebGL is not available in this browser/session, so the interactive map cannot render.");
      return;
    }
    const placed = cams.filter((c) => c.map);
    const center: [number, number] = placed[0]?.map
      ? [placed[0].map.lng, placed[0].map.lat]
      : DEFAULT_CENTER;
    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({ container: mapDiv.current, style: OSM_STYLE, center, zoom: 16 });
    } catch (err) {
      setGlError(
        `Map failed to initialize: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
    map.on("error", (e) => {
      // a WebGL context loss surfaces here; fall back rather than crash
      if (String(e?.error ?? "").toLowerCase().includes("webgl")) {
        setGlError("The map lost its WebGL context. Showing the camera coordinate list instead.");
      }
    });
    map.addControl(new maplibregl.NavigationControl({}), "top-right");
    mapRef.current = map;
    map.on("load", () => {
      map.addSource("fov", { type: "geojson", data: coneFeatures(cams) });
      map.addLayer({
        id: "fov-fill",
        type: "fill",
        source: "fov",
        paint: {
          "fill-color": ["match", ["get", "status"], "online", "#48C08A", "offline", "#E5484D", "#F2A93B"],
          "fill-opacity": 0.18,
        },
      });
    });
    map.on("click", (e) => {
      const id = placingRef.current;
      if (!id) return;
      core
        .setCameraMap(id, { lat: e.lngLat.lat, lng: e.lngLat.lng, heading: 0, fov: 60 })
        .then(() => {
          setPlacing(null);
          placingRef.current = null;
          load();
        })
        .catch(() => {});
    });
  }, [cams]);

  // redraw markers + cones when cams change
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];
    for (const c of cams) {
      if (!c.map) continue;
      const el = document.createElement("div");
      el.style.cssText = `width:14px;height:14px;border-radius:50%;border:2px solid #0E0F11;background:${STATUS_COLOR[c.status] ?? "#9aa0a6"};box-shadow:0 0 0 1px ${STATUS_COLOR[c.status] ?? "#9aa0a6"}`;
      el.title = c.name;
      const marker = new maplibregl.Marker({ element: el }).setLngLat([c.map.lng, c.map.lat]).addTo(map);
      markersRef.current.push(marker);
    }
    const src = map.getSource("fov") as maplibregl.GeoJSONSource | undefined;
    src?.setData(coneFeatures(cams));
  }, [cams]);

  const unplaced = cams.filter((c) => !c.map);

  return (
    <div className="flex h-full overflow-hidden">
      <div className="w-[240px] shrink-0 overflow-auto border-r border-line">
        <div className="border-b border-line px-3 py-2">
          <div className="text-[13px] font-semibold text-fg">Site Map</div>
          <div className="text-[11px] text-fg-muted">Place cameras on the facility map.</div>
        </div>
        {cams.map((c) => (
          <div key={c.id} className="flex items-center justify-between border-b border-line/40 px-3 py-2">
            <div>
              <div className="text-[12px] text-fg">{c.name}</div>
              <div className="mono text-[10px] text-fg-muted">{c.map ? "placed" : "not placed"}</div>
            </div>
            <button
              disabled={!user}
              onClick={() => {
                setPlacing(c.id);
                placingRef.current = c.id;
              }}
              className={`mono rounded-[2px] px-1.5 py-0.5 text-[10px] uppercase disabled:opacity-40 ${
                placing === c.id ? "bg-cyan/20 text-cyan" : "bg-raised text-fg-muted hover:text-cyan"
              }`}
            >
              {placing === c.id ? "click map" : c.map ? "move" : "place"}
            </button>
          </div>
        ))}
        {!user && (
          <div className="px-3 py-2 text-[11px] text-amber">Sign in to place cameras.</div>
        )}
        {unplaced.length > 0 && (
          <div className="px-3 py-2 text-[10px] text-fg-muted">
            {unplaced.length} camera(s) not yet placed.
          </div>
        )}
      </div>
      {glError ? (
        <div className="flex-1 overflow-auto p-4">
          <div className="mb-3 rounded-[3px] border border-amber/40 bg-amber/10 px-3 py-2 text-[12px] text-amber">
            {glError} Camera placements are shown below as coordinates.
          </div>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-line text-left text-[10px] uppercase text-fg-muted">
                <th className="py-1.5 pr-3 font-medium">Camera</th>
                <th className="py-1.5 pr-3 font-medium">Status</th>
                <th className="py-1.5 pr-3 font-medium">Latitude</th>
                <th className="py-1.5 pr-3 font-medium">Longitude</th>
                <th className="py-1.5 pr-3 font-medium">Heading</th>
              </tr>
            </thead>
            <tbody className="mono">
              {cams.map((c) => (
                <tr key={c.id} className="border-b border-line/40">
                  <td className="py-1.5 pr-3 text-fg">{c.name}</td>
                  <td className="py-1.5 pr-3" style={{ color: STATUS_COLOR[c.status] ?? "#9aa0a6" }}>
                    {c.status}
                  </td>
                  <td className="py-1.5 pr-3 text-fg-muted">{c.map ? c.map.lat.toFixed(6) : "n/a"}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{c.map ? c.map.lng.toFixed(6) : "n/a"}</td>
                  <td className="py-1.5 pr-3 text-fg-muted">{c.map ? `${c.map.heading}°` : "n/a"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={mapDiv} className="flex-1" />
      )}
    </div>
  );
}
