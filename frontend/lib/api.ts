const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type SiteType = "level" | "quality";

export interface SiteSummary {
  id: string;
  type: SiteType;
  label: string;
  lat: number;
  lon: number;
  status: string | null;
  distance_km?: number;
}

export interface LevelReading {
  date_time: string;
  value: number | null;
  quality: string | null;
}

export interface Determinand {
  determinand_code: string;
  determinand_label: string;
  unit_label: string | null;
}

export interface ChemistryObservation {
  observation_id: string;
  sample_date: string;
  determinand_code: string;
  determinand_label: string;
  result_value: number | null;
  simple_result: string | null;
  unit_label: string | null;
}

export async function fetchSites(q?: string): Promise<SiteSummary[]> {
  const params = new URLSearchParams({ limit: "10000" });
  if (q) params.set("q", q);
  const res = await fetch(`${API_BASE}/api/sites?${params}`);
  if (!res.ok) throw new Error("Failed to load sites");
  const data = await res.json();
  return data.sites;
}

export async function fetchLevelTimeseries(notation: string): Promise<LevelReading[]> {
  const res = await fetch(`${API_BASE}/api/sites/level/${encodeURIComponent(notation)}/timeseries`);
  if (!res.ok) throw new Error("Failed to load level readings");
  const data = await res.json();
  return data.readings;
}

export async function fetchQualityTimeseries(
  notation: string
): Promise<{ observations: ChemistryObservation[]; determinands: Determinand[] }> {
  const res = await fetch(`${API_BASE}/api/sites/quality/${encodeURIComponent(notation)}/timeseries`);
  if (!res.ok) throw new Error("Failed to load chemistry observations");
  return res.json();
}

export async function searchPostcode(postcode: string): Promise<{
  postcode: string;
  lat: number;
  lon: number;
  sites: SiteSummary[];
}> {
  const params = new URLSearchParams({ postcode });
  const res = await fetch(`${API_BASE}/api/search/postcode?${params}`);
  if (!res.ok) throw new Error("Postcode not found");
  return res.json();
}

export function siteDetailPath(type: SiteType, id: string) {
  return `${API_BASE}/api/sites/${type}/${encodeURIComponent(id)}`;
}
