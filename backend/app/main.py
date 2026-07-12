import math

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.db import get_connection, init_db
from app.ea_client import fetch_chemistry_observations, fetch_level_readings, now_iso

app = FastAPI(title="Groundwater Monitoring API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/api/sites")
def list_sites(bbox: str | None = None, q: str | None = None, limit: int = 5000):
    """Sites for the map. bbox format: minLon,minLat,maxLon,maxLat"""
    conn = get_connection()
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

    level_rows = conn.execute(
        f"SELECT notation, label, lat, lon, status FROM level_stations {where_clause} LIMIT ?",
        [*params, limit],
    ).fetchall()

    quality_rows = conn.execute(
        f"SELECT notation, label, lat, lon, status_label FROM quality_sites {where_clause} LIMIT ?",
        [*params, limit],
    ).fetchall()
    conn.close()

    sites = [
        {
            "id": r["notation"],
            "type": "level",
            "label": r["label"],
            "lat": r["lat"],
            "lon": r["lon"],
            "status": r["status"],
        }
        for r in level_rows
    ] + [
        {
            "id": r["notation"],
            "type": "quality",
            "label": r["label"],
            "lat": r["lat"],
            "lon": r["lon"],
            "status": r["status_label"],
        }
        for r in quality_rows
    ]
    return {"count": len(sites), "sites": sites}


@app.get("/api/sites/level/{notation}")
def get_level_station(notation: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM level_stations WHERE notation = ?", (notation,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Station not found")
    return dict(row)


@app.get("/api/sites/quality/{notation}")
def get_quality_site(notation: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM quality_sites WHERE notation = ?", (notation,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Site not found")
    return dict(row)


@app.get("/api/sites/level/{notation}/timeseries")
def get_level_timeseries(notation: str):
    conn = get_connection()
    station = conn.execute("SELECT notation FROM level_stations WHERE notation = ?", (notation,)).fetchone()
    if not station:
        conn.close()
        raise HTTPException(404, "Station not found")

    cached = conn.execute(
        "SELECT 1 FROM chemistry_fetch_log WHERE site_notation = ?", (f"level:{notation}",)
    ).fetchone()

    if not cached:
        with httpx.Client(timeout=30.0) as client:
            readings = fetch_level_readings(client, notation)
        rows = [(notation, r["date_time"], r["value"], r["quality"]) for r in readings]
        conn.executemany(
            "INSERT OR REPLACE INTO level_readings (station_notation, date_time, value, quality) VALUES (?,?,?,?)",
            rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO chemistry_fetch_log (site_notation, fetched_at) VALUES (?, ?)",
            (f"level:{notation}", now_iso()),
        )
        conn.commit()

    readings = conn.execute(
        "SELECT date_time, value, quality FROM level_readings WHERE station_notation = ? ORDER BY date_time",
        (notation,),
    ).fetchall()
    conn.close()
    return {"notation": notation, "readings": [dict(r) for r in readings]}


@app.get("/api/sites/quality/{notation}/timeseries")
def get_quality_timeseries(notation: str):
    conn = get_connection()
    site = conn.execute("SELECT notation FROM quality_sites WHERE notation = ?", (notation,)).fetchone()
    if not site:
        conn.close()
        raise HTTPException(404, "Site not found")

    cached = conn.execute(
        "SELECT 1 FROM chemistry_fetch_log WHERE site_notation = ?", (notation,)
    ).fetchone()

    if not cached:
        with httpx.Client(timeout=30.0) as client:
            observations = fetch_chemistry_observations(client, notation)
        rows = [
            (
                notation,
                o["observation_id"],
                o["sample_date"],
                o["determinand_code"],
                o["determinand_label"],
                o["result_value"],
                o["simple_result"],
                o["unit_label"],
            )
            for o in observations
        ]
        conn.executemany(
            """INSERT OR REPLACE INTO chemistry_observations
               (site_notation, observation_id, sample_date, determinand_code, determinand_label,
                result_value, simple_result, unit_label)
               VALUES (?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.execute(
            "INSERT OR REPLACE INTO chemistry_fetch_log (site_notation, fetched_at) VALUES (?, ?)",
            (notation, now_iso()),
        )
        conn.commit()

    observations = conn.execute(
        """SELECT observation_id, sample_date, determinand_code, determinand_label,
                  result_value, simple_result, unit_label
           FROM chemistry_observations WHERE site_notation = ? ORDER BY sample_date""",
        (notation,),
    ).fetchall()

    determinands = conn.execute(
        """SELECT DISTINCT determinand_code, determinand_label, unit_label
           FROM chemistry_observations WHERE site_notation = ? ORDER BY determinand_label""",
        (notation,),
    ).fetchall()
    conn.close()

    return {
        "notation": notation,
        "observations": [dict(r) for r in observations],
        "determinands": [dict(r) for r in determinands],
    }


@app.get("/api/search/postcode")
def search_postcode(postcode: str, radius_km: float = 15.0, limit: int = 50):
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"https://api.postcodes.io/postcodes/{postcode}")
    if resp.status_code != 200:
        raise HTTPException(404, "Postcode not found")
    result = resp.json()["result"]
    lat, lon = result["latitude"], result["longitude"]

    conn = get_connection()
    level_rows = conn.execute(
        "SELECT notation, label, lat, lon FROM level_stations WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    quality_rows = conn.execute(
        "SELECT notation, label, lat, lon FROM quality_sites WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchall()
    conn.close()

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
    return {
        "postcode": result["postcode"],
        "lat": lat,
        "lon": lon,
        "sites": results[:limit],
    }
