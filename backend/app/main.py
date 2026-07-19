import ast
import json
import math
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import stats
from app.db import get_client, init_db
from app.ea_client import fetch_chemistry_observations, fetch_level_readings, history_cutoff_date


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Groundwater Monitoring API", lifespan=lifespan)

allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _flatten_stats_row(row: dict) -> dict:
    """Cached stats rows store their data-quality flags as a JSON blob
    (`data_quality_flags`) alongside a plain `data_quality_label` column; the
    live-fallback path builds the same flags inline instead. Flatten the
    cached shape to match so the frontend sees one consistent stats shape
    regardless of which path served it."""
    flags_json = row.pop("data_quality_flags", None)
    label = row.pop("data_quality_label", None)
    flags = {}
    if flags_json:
        try:
            flags = json.loads(flags_json)
        except json.JSONDecodeError:
            # Rows written before refresh.py switched from str(dict) to
            # json.dumps(dict) are a Python repr, not JSON - still readable.
            flags = ast.literal_eval(flags_json)
    flags.setdefault("label", label)
    return {**row, **flags}


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/api/sites")
async def list_sites(bbox: str | None = None, q: str | None = None, limit: int = 5000):
    """Sites for the map. bbox format: minLon,minLat,maxLon,maxLat"""
    db = get_client()
    where = ["lat IS NOT NULL", "lon IS NOT NULL"]
    params: list = []

    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
        where.append("lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
        params.extend([min_lat, max_lat, min_lon, max_lon])

    if q:
        where.append("label LIKE ?")
        params.append(f"%{q}%")

    where_clause = "WHERE " + " AND ".join(where)

    level_rows = await db.execute(
        f"SELECT notation, label, lat, lon, status FROM level_stations {where_clause} LIMIT ?",
        [*params, limit],
    )
    quality_rows = await db.execute(
        f"SELECT notation, label, lat, lon, status_label FROM quality_sites {where_clause} LIMIT ?",
        [*params, limit],
    )
    await db.close()

    sites = [
        {"id": r["notation"], "type": "level", "label": r["label"], "lat": r["lat"], "lon": r["lon"], "status": r["status"]}
        for r in level_rows
    ] + [
        {"id": r["notation"], "type": "quality", "label": r["label"], "lat": r["lat"], "lon": r["lon"], "status": r["status_label"]}
        for r in quality_rows
    ]
    return {"count": len(sites), "sites": sites}


@app.get("/api/sites/level/{notation}")
async def get_level_station(notation: str):
    db = get_client()
    rs = await db.execute("SELECT * FROM level_stations WHERE notation = ?", [notation])
    await db.close()
    if not len(rs):
        raise HTTPException(404, "Station not found")
    return rs[0].asdict()


@app.get("/api/sites/quality/{notation}")
async def get_quality_site(notation: str):
    db = get_client()
    rs = await db.execute("SELECT * FROM quality_sites WHERE notation = ?", [notation])
    await db.close()
    if not len(rs):
        raise HTTPException(404, "Site not found")
    return rs[0].asdict()


@app.get("/api/sites/level/{notation}/timeseries")
async def get_level_timeseries(notation: str):
    db = get_client()
    station = await db.execute("SELECT notation FROM level_stations WHERE notation = ?", [notation])
    if not len(station):
        await db.close()
        raise HTTPException(404, "Station not found")

    readings_rs = await db.execute(
        "SELECT date_time, value, quality, is_outlier FROM level_readings WHERE station_notation = ? ORDER BY date_time",
        [notation],
    )
    readings = [r.asdict() for r in readings_rs]
    stats_rs = await db.execute("SELECT * FROM level_station_stats WHERE station_notation = ?", [notation])
    site_stats = _flatten_stats_row(stats_rs[0].asdict()) if len(stats_rs) else None
    await db.close()

    sync_pending = False
    if not readings:
        # Nightly job hasn't reached this site yet - try a live fetch, don't
        # persist. The EA APIs are sometimes very slow (tens of seconds) for
        # sites with a lot of history, so bound the wait and fail soft rather
        # than hanging the request past Vercel's function timeout.
        try:
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                fetched = await fetch_level_readings(http_client, notation, since=history_cutoff_date())
        except httpx.HTTPError:
            fetched = []
            sync_pending = True
        readings = [{**r, "is_outlier": False} for r in fetched]
        values = [r["value"] for r in readings if r["value"] is not None]
        dates = [r["date_time"] for r in readings if r["value"] is not None]
        if values:
            summary = stats.summarize(values)
            trend = stats.trend(dates, values)
            site_stats = {
                **summary,
                **trend,
                "latest_value": values[-1],
                "latest_date": dates[-1],
                "first_date": dates[0],
                **stats.data_quality_flags(
                    count=len(values), censored_count=0, latest_date=dates[-1],
                    stale_days_threshold=stats.STALE_DAYS_LEVEL,
                ),
            }

    return {"notation": notation, "readings": readings, "stats": site_stats, "sync_pending": sync_pending}


def _compute_live_quality_stats(notation: str, observations: list[dict]) -> list[dict]:
    """Same per-determinand summary/trend computation refresh.py's
    recompute_quality_stats does, but in-memory rather than written to
    quality_site_stats. Used whenever a site has observations but no cached
    stats row yet - either because the nightly job hasn't reached its Phase 3
    recompute for this site, or because the observations were just fetched
    live below."""
    by_determinand: dict[str, list[dict]] = {}
    for o in observations:
        by_determinand.setdefault(o["determinand_code"], []).append(o)

    site_stats = []
    for code, obs in by_determinand.items():
        numeric = [o for o in obs if o["result_value"] is not None]
        values = [o["result_value"] for o in numeric]
        dates = [o["sample_date"] for o in numeric]
        censored_count = sum(1 for o in obs if o["result_value"] is None and o["simple_result"])
        summary = stats.summarize(values)
        trend = stats.trend(dates, values) if values else {
            "trend_direction": "insufficient_data", "trend_slope_per_year": None, "trend_p_value": None,
        }
        site_stats.append({
            "site_notation": notation,
            "determinand_code": code,
            "determinand_label": obs[0]["determinand_label"],
            "unit_label": obs[0]["unit_label"],
            **summary,
            **trend,
            "censored_count": censored_count,
            "latest_value": values[-1] if values else None,
            "latest_date": obs[-1]["sample_date"],
            "first_date": obs[0]["sample_date"],
            **stats.data_quality_flags(
                count=len(obs), censored_count=censored_count, latest_date=obs[-1]["sample_date"],
                stale_days_threshold=stats.STALE_DAYS_QUALITY,
            ),
        })
    return site_stats


@app.get("/api/sites/quality/{notation}/timeseries")
async def get_quality_timeseries(notation: str):
    db = get_client()
    site = await db.execute("SELECT notation FROM quality_sites WHERE notation = ?", [notation])
    if not len(site):
        await db.close()
        raise HTTPException(404, "Site not found")

    obs_rs = await db.execute(
        """SELECT observation_id, sample_date, determinand_code, determinand_label,
                  result_value, simple_result, unit_label, is_outlier
           FROM chemistry_observations WHERE site_notation = ? ORDER BY sample_date""",
        [notation],
    )
    observations = [r.asdict() for r in obs_rs]

    determinands_rs = await db.execute(
        """SELECT DISTINCT determinand_code, determinand_label, unit_label
           FROM chemistry_observations WHERE site_notation = ? ORDER BY determinand_label""",
        [notation],
    )
    determinands = [r.asdict() for r in determinands_rs]

    stats_rs = await db.execute("SELECT * FROM quality_site_stats WHERE site_notation = ?", [notation])
    site_stats = [_flatten_stats_row(r.asdict()) for r in stats_rs]
    await db.close()

    sync_pending = False
    if not observations:
        # Nightly job hasn't reached this site yet - try a live fetch, don't
        # persist. The EA Water Quality API is often very slow (tens of
        # seconds, occasionally over a minute) for sites with a lot of
        # sampling history, regardless of page size or date filtering, so
        # bound the wait and fail soft rather than hanging the request past
        # Vercel's function timeout.
        try:
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                fetched = await fetch_chemistry_observations(http_client, notation, since=history_cutoff_date())
        except httpx.HTTPError:
            fetched = []
            sync_pending = True
        observations = [{**o, "is_outlier": False} for o in fetched]

        seen = {}
        for o in observations:
            seen.setdefault(o["determinand_code"], {"label": o["determinand_label"], "unit": o["unit_label"]})
        determinands = [
            {"determinand_code": code, "determinand_label": v["label"], "unit_label": v["unit"]}
            for code, v in seen.items()
        ]

    if not site_stats and observations:
        # Either the fetch above just ran, or the nightly job's Phase 3
        # recompute simply hasn't reached this site yet even though its
        # observations are already synced - either way, compute the stats
        # in-memory so the frontend still gets a trend rather than nothing.
        site_stats = _compute_live_quality_stats(notation, observations)

    return {
        "notation": notation,
        "observations": observations,
        "determinands": determinands,
        "stats": site_stats,
        "sync_pending": sync_pending,
    }


@app.get("/api/search/postcode")
async def search_postcode(postcode: str, radius_km: float = 15.0, limit: int = 50):
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        resp = await http_client.get(f"https://api.postcodes.io/postcodes/{postcode}")
    if resp.status_code != 200:
        raise HTTPException(404, "Postcode not found")
    result = resp.json()["result"]
    lat, lon = result["latitude"], result["longitude"]

    db = get_client()
    level_rows = await db.execute(
        "SELECT notation, label, lat, lon FROM level_stations WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    quality_rows = await db.execute(
        "SELECT notation, label, lat, lon FROM quality_sites WHERE lat IS NOT NULL AND lon IS NOT NULL"
    )
    await db.close()

    results = []
    for r in level_rows:
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= radius_km:
            results.append({"id": r["notation"], "type": "level", "label": r["label"], "lat": r["lat"], "lon": r["lon"], "distance_km": round(d, 2)})
    for r in quality_rows:
        d = haversine_km(lat, lon, r["lat"], r["lon"])
        if d <= radius_km:
            results.append({"id": r["notation"], "type": "quality", "label": r["label"], "lat": r["lat"], "lon": r["lon"], "distance_km": round(d, 2)})

    results.sort(key=lambda x: x["distance_km"])
    return {"postcode": result["postcode"], "lat": lat, "lon": lon, "sites": results[:limit]}
