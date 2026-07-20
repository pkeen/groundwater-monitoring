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
import type {
  SiteSummary,
  Determinand,
  ChemistryObservation,
  LevelReading,
  SiteStats,
} from "@/lib/api";
import { fetchLevelTimeseries, fetchQualityTimeseries } from "@/lib/api";

interface Props {
  site: SiteSummary;
  onClose: () => void;
  expanded: boolean;
  onToggleExpand: () => void;
}

const QUALITY_LABEL_STYLES: Record<string, string> = {
  Good: "bg-green-100 text-green-800",
  "Limited data": "bg-yellow-100 text-yellow-800",
  Stale: "bg-orange-100 text-orange-800",
  "Mostly non-detect": "bg-slate-100 text-slate-700",
  "No data": "bg-gray-100 text-gray-600",
};

function TrendBadge({ stat }: { stat: SiteStats }) {
  if (stat.trend_direction === "insufficient_data" || stat.trend_slope_per_year === null) {
    return <span className="text-xs text-gray-400">Not enough data for a trend</span>;
  }
  const isIncreasing = stat.trend_direction === "increasing";
  const isDecreasing = stat.trend_direction === "decreasing";
  const arrow = isIncreasing ? "↑" : isDecreasing ? "↓" : "→";
  const color = isIncreasing ? "text-red-600" : isDecreasing ? "text-blue-600" : "text-gray-500";
  const significant = stat.trend_p_value !== null && stat.trend_p_value < 0.05;
  return (
    <span className={`text-xs font-medium ${color}`}>
      {arrow} {stat.trend_direction?.replace("_", " ")}
      {stat.trend_slope_per_year !== null && (
        <> ({stat.trend_slope_per_year >= 0 ? "+" : ""}{stat.trend_slope_per_year.toFixed(3)}/yr)</>
      )}
      {!significant && <span className="text-gray-400"> (not significant)</span>}
    </span>
  );
}

function StatsSummary({ stat, unit }: { stat: SiteStats; unit?: string | null }) {
  const labelStyle = QUALITY_LABEL_STYLES[stat.label] ?? "bg-gray-100 text-gray-600";
  return (
    <div className="rounded border border-gray-200 bg-gray-50 p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className={`rounded px-2 py-0.5 text-xs font-medium ${labelStyle}`}>{stat.label}</span>
        <TrendBadge stat={stat} />
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-700">
        <div>Latest: <span className="font-medium">{stat.latest_value ?? "—"} {unit ?? ""}</span></div>
        <div>Samples: <span className="font-medium">{stat.count}</span></div>
        <div>Min / Max: <span className="font-medium">{stat.min_value ?? "—"} / {stat.max_value ?? "—"}</span></div>
        <div>Mean: <span className="font-medium">{stat.mean_value?.toFixed(2) ?? "—"}</span></div>
        {stat.outlier_count !== undefined && stat.outlier_count > 0 && (
          <div className="col-span-2 text-amber-700">
            {stat.outlier_count} outlier{stat.outlier_count === 1 ? "" : "s"} flagged (highlighted red on chart)
          </div>
        )}
        {stat.censored_fraction !== undefined && stat.censored_fraction > 0 && (
          <div className="col-span-2 text-gray-500">
            {Math.round(stat.censored_fraction * 100)}% non-detect (below detection limit)
          </div>
        )}
      </div>
    </div>
  );
}

export default function SitePanel({ site, onClose, expanded, onToggleExpand }: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [syncPending, setSyncPending] = useState(false);

  const [levelReadings, setLevelReadings] = useState<LevelReading[]>([]);
  const [levelStats, setLevelStats] = useState<SiteStats | null>(null);
  const [observations, setObservations] = useState<ChemistryObservation[]>([]);
  const [determinands, setDeterminands] = useState<Determinand[]>([]);
  const [qualityStats, setQualityStats] = useState<SiteStats[]>([]);
  const [selectedDeterminand, setSelectedDeterminand] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSyncPending(false);
    setSelectedDeterminand(null);

    if (site.type === "level") {
      fetchLevelTimeseries(site.id)
        .then(({ readings, stats, sync_pending }) => {
          if (cancelled) return;
          setLevelReadings(readings);
          setLevelStats(stats);
          setSyncPending(!!sync_pending);
        })
        .catch(() => !cancelled && setError("Could not load level readings."))
        .finally(() => !cancelled && setLoading(false));
    } else {
      fetchQualityTimeseries(site.id)
        .then(({ observations, determinands, stats, sync_pending }) => {
          if (cancelled) return;
          setObservations(observations);
          setDeterminands(determinands);
          setQualityStats(stats);
          setSelectedDeterminand(determinands[0]?.determinand_code ?? null);
          setSyncPending(!!sync_pending);
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
        .map((r) => ({ date: r.date_time.slice(0, 10), value: r.value, isOutlier: !!r.is_outlier }));
    }
    return observations
      .filter((o) => o.determinand_code === selectedDeterminand && o.result_value !== null)
      .map((o) => ({ date: o.sample_date.slice(0, 10), value: o.result_value, isOutlier: !!o.is_outlier }));
  }, [site.type, levelReadings, observations, selectedDeterminand]);

  const currentUnit =
    site.type === "quality"
      ? determinands.find((d) => d.determinand_code === selectedDeterminand)?.unit_label
      : "mAOD";

  const currentQualityStat = qualityStats.find((s) => s.determinand_code === selectedDeterminand);

  const header = (
    <div className={`flex items-start justify-between gap-2 ${expanded ? "border-b border-gray-200 p-4" : ""}`}>
      <div>
        <span
          className={`inline-block rounded px-2 py-0.5 text-xs font-medium text-white ${
            site.type === "level" ? "bg-blue-600" : "bg-green-600"
          }`}
        >
          {site.type === "level" ? "Level station" : "Quality sampling point"}
        </span>
        <h2 className={expanded ? "mt-1 text-2xl font-semibold leading-tight" : "mt-1 text-lg font-semibold leading-tight"}>
          {site.label}
        </h2>
        <p className="text-xs text-gray-500">{site.id}</p>
      </div>
      <div className="flex items-center gap-1">
        <button
          onClick={onToggleExpand}
          className="rounded px-2 py-1 text-xs font-medium text-gray-500 hover:bg-gray-100 hover:text-gray-700"
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
        <button
          onClick={onClose}
          className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
          aria-label="Close"
        >
          ✕
        </button>
      </div>
    </div>
  );

  const statusMessages = (
    <>
      {loading && <p className="text-sm text-gray-500">Loading time series...</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!loading && !error && syncPending && (
        <p className="text-sm text-amber-600">
          This site hasn&apos;t been synced yet and the live data source is responding too
          slowly to load right now. It will be available after the next nightly refresh.
        </p>
      )}
    </>
  );

  const determinandSelector = !loading && !error && site.type === "quality" && (
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
  );

  const statsBlock = (
    <>
      {!loading && !error && site.type === "level" && levelStats && (
        <StatsSummary stat={levelStats} unit="mAOD" />
      )}
      {!loading && !error && site.type === "quality" && currentQualityStat && (
        <StatsSummary stat={currentQualityStat} unit={currentUnit} />
      )}
    </>
  );

  const footerText = (
    <>
      {!loading && !error && site.type === "quality" && (
        <p className="text-xs text-gray-400">{observations.length} total observations across {determinands.length} determinands.</p>
      )}
      {!loading && !error && site.type === "level" && (
        <p className="text-xs text-gray-400">{levelReadings.length} readings.</p>
      )}
    </>
  );

  const chart = !loading && !error && chartData.length > 0 && (
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
          strokeWidth={1.5}
          dot={(props) => {
            const { key, cx, cy, payload } = props;
            if (!payload.isOutlier) return <span key={key} />;
            return <circle key={key} cx={cx} cy={cy} r={4} fill="#dc2626" stroke="#dc2626" />;
          }}
        />
      </LineChart>
    </ResponsiveContainer>
  );

  const emptyChartMessage = !loading && !error && !syncPending && chartData.length === 0 && (
    <p className="text-sm text-gray-500">No numeric readings available to plot.</p>
  );

  if (expanded) {
    return (
      <div className="flex h-full w-full flex-col">
        {header}
        <div className="flex flex-1 gap-6 overflow-hidden p-4">
          <div className="flex w-80 shrink-0 flex-col gap-3 overflow-y-auto">
            {statusMessages}
            {determinandSelector}
            {statsBlock}
            {footerText}
          </div>
          <div className="flex min-w-0 flex-1 flex-col">
            {emptyChartMessage}
            {chart && <div className="min-h-0 flex-1">{chart}</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col gap-3 overflow-y-auto p-4">
      {header}
      {statusMessages}
      {determinandSelector}
      {statsBlock}
      {emptyChartMessage}
      {chart && <div className="h-72 w-full">{chart}</div>}
      {footerText}
    </div>
  );
}
