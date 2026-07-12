"use client";

import { useEffect, useMemo } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import type { SiteSummary } from "@/lib/api";

const UK_CENTER: [number, number] = [54.0, -2.5];

function FlyTo({ center }: { center: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (center) {
      map.flyTo(center, 12, { duration: 0.8 });
    }
  }, [center, map]);
  return null;
}

interface Props {
  sites: SiteSummary[];
  selectedId: string | null;
  onSelect: (site: SiteSummary) => void;
  flyToCenter: [number, number] | null;
}

const COLORS: Record<string, string> = {
  level: "#2563eb",
  quality: "#16a34a",
};

export default function GroundwaterMap({ sites, selectedId, onSelect, flyToCenter }: Props) {
  const markers = useMemo(() => sites, [sites]);

  return (
    <MapContainer
      center={UK_CENTER}
      zoom={6}
      preferCanvas
      style={{ height: "100%", width: "100%" }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FlyTo center={flyToCenter} />
      {markers.map((site) => (
        <CircleMarker
          key={`${site.type}-${site.id}`}
          center={[site.lat, site.lon]}
          radius={site.id === selectedId ? 7 : 4}
          pathOptions={{
            color: COLORS[site.type],
            fillColor: COLORS[site.type],
            fillOpacity: site.id === selectedId ? 1 : 0.6,
            weight: site.id === selectedId ? 2 : 1,
          }}
          eventHandlers={{ click: () => onSelect(site) }}
        >
          <Tooltip>{site.label}</Tooltip>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
