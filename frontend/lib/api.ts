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
  is_outlier: boolean | number;
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
  is_outlier: boolean | number;
}

export interface SiteStats {
  site_notation?: string;
  determinand_code?: string;
  determinand_label?: string;
  unit_label?: string | null;
  count: number;
  censored_count?: number;
  min_value: number | null;
  max_value: number | null;
  mean_value: number | null;
  median_value: number | null;
  stddev_value: number | null;
  latest_value: number | null;
  latest_date: string | null;
  first_date: string | null;
  trend_direction: string | null;
  trend_slope_per_year: number | null;
  trend_p_value: number | null;
  outlier_count?: number;
  is_sparse?: boolean;
  is_stale?: boolean;
  censored_fraction?: number;
  days_since_latest?: number | null;
  label: string;
}

export async function fetchSites(q?: string): Promise<SiteSummary[]> {
  const params = new URLSearchParams({ limit: "10000" });
  if (q) params.set("q", q);
  const res = await fetch(`${API_BASE}/api/sites?${params}`);
  if (!res.ok) throw new Error("Failed to load sites");
  const data = await res.json();
  return data.sites;
}

export async function fetchLevelTimeseries(
  notation: string
): Promise<{ readings: LevelReading[]; stats: SiteStats | null }> {
  const res = await fetch(`${API_BASE}/api/sites/level/${encodeURIComponent(notation)}/timeseries`);
  if (!res.ok) throw new Error("Failed to load level readings");
  return res.json();
}

export async function fetchQualityTimeseries(
  notation: string
): Promise<{ observations: ChemistryObservation[]; determinands: Determinand[]; stats: SiteStats[] }> {
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
