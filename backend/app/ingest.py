"""Bulk-download groundwater site registries from the EA APIs into SQLite.

Two independent registries are ingested (they use different IDs and there is
no public crosswalk between them):
  - Hydrology API: groundwater level monitoring stations
  - Water Quality Archive API: groundwater chemistry sampling points

Time-series readings are NOT bulk-downloaded here (that's millions of rows
across ~5,600+ quality sites and hundreds of level stations) - they are
fetched on demand per-site by the backend and cached in SQLite.
"""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import get_connection, init_db

HYDROLOGY_BASE = "https://environment.data.gov.uk/hydrology"
WQ_BASE = "https://environment.data.gov.uk/water-quality"

GROUNDWATER_SITE_TYPES = ["BA", "BB", "BC", "BD", "BE", "BH", "BL", "BZ"]


def ingest_level_stations(client: httpx.Client) -> int:
    conn = get_connection()
    offset = 0
    limit = 500
    total = 0
    while True:
        resp = client.get(
            f"{HYDROLOGY_BASE}/id/stations",
            params={
                "observedProperty": "groundwaterLevel",
                "_limit": limit,
                "_offset": offset,
            },
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            break

        rows = []
        for s in items:
            measure_notation = measure_parameter = None
            measure_period = None
            measures = s.get("measures", [])
            level_measure = next((m for m in measures if m.get("parameter") == "level"), None)
            if level_measure:
                measure_notation = level_measure.get("@id", "").rsplit("/", 1)[-1]
                measure_parameter = level_measure.get("parameter")
                measure_period = level_measure.get("period")

            rows.append(
                (
                    s.get("notation"),
                    s.get("label"),
                    s.get("lat"),
                    s.get("long"),
                    s.get("easting"),
                    s.get("northing"),
                    s.get("stationGuid"),
                    s.get("wiskiID"),
                    s.get("aquifer"),
                    s.get("boreholeDepth"),
                    s.get("dateOpened"),
                    (s.get("status") or [{}])[0].get("label") if s.get("status") else None,
                    measure_notation,
                    measure_parameter,
                    measure_period,
                )
            )

        conn.executemany(
            """INSERT OR REPLACE INTO level_stations
               (notation, label, lat, lon, easting, northing, station_guid, wiski_id,
                aquifer, borehole_depth, date_opened, status,
                measure_notation, measure_parameter, measure_period)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        total += len(rows)
        print(f"  level stations: {total} so far...")
        offset += limit
        if len(items) < limit:
            break
    conn.close()
    return total


def ingest_quality_sites(client: httpx.Client) -> int:
    conn = get_connection()
    skip = 0
    limit = 250
    total = 0
    headers = {
        "Accept": "application/ld+json",
        "Accept-Crs": "http://www.opengis.net/def/crs/EPSG/0/4326",
    }
    type_filter = ",".join(GROUNDWATER_SITE_TYPES)
    while True:
        resp = client.get(
            f"{WQ_BASE}/sampling-point",
            params={"samplingPointType": type_filter, "limit": limit, "skip": skip},
            headers=headers,
        )
        resp.raise_for_status()
        members = resp.json().get("member", [])
        if not members:
            break

        rows = []
        for m in members:
            lat = lon = None
            wkt = (m.get("geometry") or {}).get("asWKT", "")
            if wkt.startswith("POINT("):
                coords = wkt[len("POINT("):wkt.index(")")].split()
                if len(coords) == 2:
                    lon, lat = float(coords[0]), float(coords[1])

            site_type = m.get("samplingPointType") or {}
            status = m.get("samplingPointStatus") or {}
            region = m.get("region") or {}
            area = m.get("area") or {}

            rows.append(
                (
                    m.get("notation"),
                    m.get("prefLabel") or m.get("altLabel"),
                    lat,
                    lon,
                    None,
                    None,
                    site_type.get("notation"),
                    site_type.get("prefLabel"),
                    status.get("notation"),
                    status.get("prefLabel"),
                    region.get("prefLabel"),
                    area.get("prefLabel"),
                )
            )

        conn.executemany(
            """INSERT OR REPLACE INTO quality_sites
               (notation, label, lat, lon, easting, northing, site_type_code, site_type_label,
                status_code, status_label, region_label, area_label)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        total += len(rows)
        print(f"  quality sites: {total} so far...")
        skip += limit
        if len(members) < limit:
            break
        time.sleep(0.1)
    conn.close()
    return total


def main() -> None:
    init_db()
    with httpx.Client(timeout=30.0) as client:
        print("Ingesting groundwater level stations (Hydrology API)...")
        n_level = ingest_level_stations(client)
        print(f"Done: {n_level} level stations.\n")

        print("Ingesting groundwater quality sampling points (WQ Archive API)...")
        n_quality = ingest_quality_sites(client)
        print(f"Done: {n_quality} quality sites.")


if __name__ == "__main__":
    main()
