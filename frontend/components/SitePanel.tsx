"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from "recharts";
import type { SiteSummary, Determinand, ChemistryObservation, LevelReading } from "@/lib/api";
import { fetchLevelTimeseries, fetchQualityTimeseries } from "@/lib/api";

interface Props {
  site: SiteSummary;
  onClose: () => void;
}

export default function SitePanel({ site, onClose }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [levelReadings, setLevelReadings] = useState<LevelReading[]>([]);
  const [observations, setObservations] = useState<ChemistryObservation[]>([]);
  const [determinands, setDeterminands] = useState<Determinand[]>([]);
  const [selectedDeterminand, setSelectedDeterminand] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelectedDeterminand(null);

    if (site.type === "level") {
      fetchLevelTimeseries(site.id)
        .then((readings) => {
          if (!cancelled) setLevelReadings(readings);
        })
        .catch(() => !cancelled && setError("Could not load level readings."))
        .finally(() => !cancelled && setLoading(false));
    } else {
      fetchQualityTimeseries(site.id)
        .then(({ observations, determinands }) => {
          if (cancelled) return;
          setObservations(observations);
          setDeterminands(determinands);
          setSelectedDeterminand(determinands[0]?.determinand_code ?? null);
        })
        .catch(() => !cancelled && setError("Could not load chemistry observations."))
        .finally(() => !cancelled && setLoading(false));
    }

    return () => {
      cancelled = true;
    };
  }, [site]);

  const chartData = useMemo(() => {
    if (site.type === "level") {
      return levelReadings
        .filter((r) => r.value !== null)
        .map((r) => ({ date: r.date_time.slice(0, 10), value: r.value }));
    }
    return observations
      .filter((o) => o.determinand_code === selectedDeterminand && o.result_value !== null)
      .map((o) => ({ date: o.sample_date.slice(0, 10), value: o.result_value }));
  }, [site.type, levelReadings, observations, selectedDeterminand]);

  const currentUnit =
    site.type === "quality"
      ? determinands.find((d) => d.determinand_code === selectedDeterminand)?.unit_label
      : "mAOD";

  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-y-auto p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span
            className={`inline-block rounded px-2 py-0.5 text-xs font-medium text-white ${
              site.type === "level" ? "bg-blue-600" : "bg-green-600"
            }`}
          >
            {site.type === "level" ? "Level station" : "Quality sampling point"}
          </span>
          <h2 className="mt-1 text-lg font-semibold leading-tight">{site.label}</h2>
          <p className="text-xs text-gray-500">{site.id}</p>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          aria-label="Close"
        >
          ✕
        </button>
      </div>

      {loading && <p className="text-sm text-gray-500">Loading time series...</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {!loading && !error && site.type === "quality" && (
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600">Determinand</label>
          <select
            className="w-full rounded border border-gray-300 p-1.5 text-sm"
            value={selectedDeterminand ?? ""}
            onChange={(e) => setSelectedDeterminand(e.target.value)}
          >
            {determinands.map((d) => (
              <option key={d.determinand_code} value={d.determinand_code}>
                {d.determinand_label}
              </option>
            ))}
          </select>
        </div>
      )}

      {!loading && !error && chartData.length === 0 && (
        <p className="text-sm text-gray-500">No numeric readings available to plot.</p>
      )}

      {!loading && !error && chartData.length > 0 && (
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={30} />
              <YAxis
                tick={{ fontSize: 10 }}
                label={{ value: currentUnit ?? "", angle: -90, position: "insideLeft", fontSize: 11 }}
              />
              <Tooltip />
              <Line
                type="monotone"
                dataKey="value"
                stroke={site.type === "level" ? "#2563eb" : "#16a34a"}
                dot={false}
                strokeWidth={1.5}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {!loading && !error && site.type === "quality" && (
        <p className="text-xs text-gray-400">{observations.length} total observations across {determinands.length} determinands.</p>
      )}
      {!loading && !error && site.type === "level" && (
        <p className="text-xs text-gray-400">{levelReadings.length} readings.</p>
      )}
    </div>
  );
}
