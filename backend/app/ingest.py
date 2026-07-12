"""Sync the site registries (metadata only, not time-series readings) from
the EA APIs into the database. Cheap and fast (~9,200 rows total) - safe to
run every night as step 1 of the refresh job, ahead of the much heavier
per-site reading sync.

Two independent registries are ingested (they use different IDs and there is
no public crosswalk between them):
  - Hydrology API: groundwater level monitoring stations
  - Water Quality Archive API: groundwater chemistry sampling points
"""
import asyncio

import httpx

from app.db import get_client, init_db

HYDROLOGY_BASE = "https://environment.data.gov.uk/hydrology"
WQ_BASE = "https://environment.data.gov.uk/water-quality"

GROUNDWATER_SITE_TYPES = ["BA", "BB", "BC", "BD", "BE", "BH", "BL", "BZ"]


async def ingest_level_stations(client: httpx.AsyncClient) -> int:
    db = get_client()
    offset = 0
    limit = 500
    total = 0
    try:
        while True:
            resp = await client.get(
                f"{HYDROLOGY_BASE}/id/stations",
                params={"observedProperty": "groundwaterLevel", "_limit": limit, "_offset": offset},
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            if not items:
                break

            statements = []
            for s in items:
                measure_notation = measure_parameter = None
                measure_period = None
                measures = s.get("measures", [])
                level_measure = next((m for m in measures if m.get("parameter") == "level"), None)
                if level_measure:
                    measure_notation = level_measure.get("@id", "").rsplit("/", 1)[-1]
                    measure_parameter = level_measure.get("parameter")
                    measure_period = level_measure.get("period")

                statements.append((
                    """INSERT OR REPLACE INTO level_stations
                       (notation, label, lat, lon, easting, northing, station_guid, wiski_id,
                        aquifer, borehole_depth, date_opened, status,
                        measure_notation, measure_parameter, measure_period)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
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
                    ],
                ))

            await db.batch(statements)
            total += len(statements)
            offset += limit
            if len(items) < limit:
                break
    finally:
        await db.close()
    return total


async def ingest_quality_sites(client: httpx.AsyncClient) -> int:
    db = get_client()
    skip = 0
    limit = 250
    total = 0
    headers = {
        "Accept": "application/ld+json",
        "Accept-Crs": "http://www.opengis.net/def/crs/EPSG/0/4326",
    }
    type_filter = ",".join(GROUNDWATER_SITE_TYPES)
    try:
        while True:
            resp = await client.get(
                f"{WQ_BASE}/sampling-point",
                params={"samplingPointType": type_filter, "limit": limit, "skip": skip},
                headers=headers,
            )
            resp.raise_for_status()
            members = resp.json().get("member", [])
            if not members:
                break

            statements = []
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

                statements.append((
                    """INSERT OR REPLACE INTO quality_sites
                       (notation, label, lat, lon, easting, northing, site_type_code, site_type_label,
                        status_code, status_label, region_label, area_label)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [
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
                    ],
                ))

            await db.batch(statements)
            total += len(statements)
            skip += limit
            if len(members) < limit:
                break
    finally:
        await db.close()
    return total


async def main() -> None:
    await init_db()
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("Ingesting groundwater level stations (Hydrology API)...")
        n_level = await ingest_level_stations(client)
        print(f"Done: {n_level} level stations.\n")

        print("Ingesting groundwater quality sampling points (WQ Archive API)...")
        n_quality = await ingest_quality_sites(client)
        print(f"Done: {n_quality} quality sites.")


if __name__ == "__main__":
    asyncio.run(main())
