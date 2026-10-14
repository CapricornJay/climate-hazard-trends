import L from "leaflet";
import { useEffect, useRef, useState } from "react";
import { CircleMarker, GeoJSON, MapContainer, Rectangle, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { Analysis, RegionSelection } from "../types";
import "leaflet/dist/leaflet.css";

type Mode = "point" | "box" | "none";

interface Props {
  region: RegionSelection | null;
  analysis: Analysis | null;
  mode: Mode;
  onPick: (r: RegionSelection) => void;
}

const round = (v: number, step = 0.01) => Math.round(v / step) * step;

function boundsOf(region: RegionSelection | null, analysis: Analysis | null): L.LatLngBounds | null {
  if (analysis) {
    const [s, w, n, e] = analysis.region.bbox;
    return L.latLngBounds([s, w], [n, e]).pad(0.3);
  }
  if (!region) return null;
  if (region.kind === "point") return L.latLng(region.lat, region.lon).toBounds(60_000);
  if (region.kind === "box") return L.latLngBounds([region.south, region.west], [region.north, region.east]).pad(0.2);
  if (region.bbox) {
    const [s, w, n, e] = region.bbox;
    return L.latLngBounds([s, w], [n, e]).pad(0.1);
  }
  return null;
}

function FitBounds({ bounds }: { bounds: L.LatLngBounds | null }) {
  const map = useMap();
  const key = bounds?.toBBoxString();
  useEffect(() => {
    if (bounds) map.flyToBounds(bounds, { duration: 0.6, maxZoom: 9 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);
  return null;
}

function PointPicker({ onPick }: { onPick: Props["onPick"] }) {
  useMapEvents({
    click(e) {
      const lat = round(e.latlng.lat);
      const lon = round(L.Util.wrapNum(e.latlng.lng, [-180, 180], true));
      onPick({ kind: "point", lat, lon, label: `${lat.toFixed(2)}, ${lon.toFixed(2)}` });
    },
  });
  return null;
}

/** Press and drag to draw a box. Map panning is switched off while this is active. */
function BoxDrawer({ onPick }: { onPick: Props["onPick"] }) {
  const map = useMap();
  const start = useRef<L.LatLng | null>(null);
  const [preview, setPreview] = useState<L.LatLngBounds | null>(null);

  useEffect(() => {
    const container = map.getContainer();
    map.dragging.disable();
    container.style.cursor = "crosshair";
    const down = (e: L.LeafletMouseEvent) => {
      start.current = e.latlng;
      setPreview(L.latLngBounds(e.latlng, e.latlng));
    };
    const move = (e: L.LeafletMouseEvent) => {
      if (start.current) setPreview(L.latLngBounds(start.current, e.latlng));
    };
    const up = (e: L.LeafletMouseEvent) => {
      if (!start.current) return;
      const b = L.latLngBounds(start.current, e.latlng);
      start.current = null;
      setPreview(null);
      if (Math.abs(b.getNorth() - b.getSouth()) < 0.05 || Math.abs(b.getEast() - b.getWest()) < 0.05) return;
      onPick({
        kind: "box",
        south: round(b.getSouth()),
        west: round(b.getWest()),
        north: round(b.getNorth()),
        east: round(b.getEast()),
      });
    };
    map.on("mousedown", down);
    map.on("mousemove", move);
    map.on("mouseup", up);
    return () => {
      map.off("mousedown", down);
      map.off("mousemove", move);
      map.off("mouseup", up);
      map.dragging.enable();
      container.style.cursor = "";
    };
  }, [map, onPick]);

  return preview ? <Rectangle bounds={preview} pathOptions={{ color: "#0f766e", weight: 1.5, dashArray: "4 4" }} /> : null;
}

export default function RegionMap({ region, analysis, mode, onPick }: Props) {
  const shown = analysis ? null : region;
  const station = analysis?.source.id === "ghcn" ? analysis.region.station : undefined;
  const gridAnalysis = analysis && !station ? analysis : null;
  const geometryKey = analysis?.region.key ?? (region?.kind === "admin" ? `${region.osm_type}${region.osm_id}` : "");
  const geometry = analysis?.region.geometry ?? (region?.kind === "admin" ? region.geometry : undefined);
  const style = { color: "#0f766e", weight: 1.5, fillOpacity: 0.08 };

  return (
    <MapContainer center={[30, 0]} zoom={2} minZoom={2} worldCopyJump className="map" attributionControl>
      <TileLayer
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        maxZoom={12}
        className="basemap"
      />
      <FitBounds bounds={boundsOf(region, analysis)} />
      {mode === "point" && <PointPicker onPick={onPick} />}
      {mode === "box" && <BoxDrawer onPick={onPick} />}

      {geometry && geometry.type !== "Point" && <GeoJSON key={geometryKey} data={geometry} style={style} interactive={false} />}

      {shown?.kind === "box" && (
        <Rectangle bounds={[[shown.south, shown.west], [shown.north, shown.east]]} pathOptions={style} interactive={false} />
      )}
      {shown?.kind === "point" && (
        <CircleMarker center={[shown.lat, shown.lon]} radius={7} pathOptions={{ color: "#0f766e", weight: 2, fillOpacity: 0.5 }} />
      )}

      {gridAnalysis?.region.kind === "point" && (
        <Rectangle bounds={[[gridAnalysis.region.bbox[0], gridAnalysis.region.bbox[1]], [gridAnalysis.region.bbox[2], gridAnalysis.region.bbox[3]]]}
          pathOptions={{ ...style, dashArray: "3 3" }}>
          <Tooltip sticky>ERA5 grid cell used (0.25°)</Tooltip>
        </Rectangle>
      )}
      {gridAnalysis?.region.points.map((p) => (
        <CircleMarker key={`${p.lat},${p.lon}`} center={[p.lat, p.lon]} radius={4}
          pathOptions={{ color: "#7c2d12", weight: 1, fillColor: "#ea580c", fillOpacity: 0.9 }}>
          <Tooltip>
            Sample point {p.lat.toFixed(2)}, {p.lon.toFixed(2)}
            {p.elevation != null && <> · {Math.round(p.elevation)} m</>}
          </Tooltip>
        </CircleMarker>
      ))}
      {station && (
        <CircleMarker center={[station.lat, station.lon]} radius={6}
          pathOptions={{ color: "#1e3a8a", weight: 2, fillColor: "#3b82f6", fillOpacity: 0.9 }}>
          <Tooltip permanent direction="right">{station.name} ({station.distance_km} km)</Tooltip>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
