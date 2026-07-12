"""Async fetchers for EA time-series data. Used both by the on-demand
fallback path in main.py (single site, one-off) and by the nightly
refresh job (thousands of sites, run with bounded concurrency)."""
from datetime import datetime, timezone

import httpx

HYDROLOGY_BASE = "https://environment.data.gov.uk/hydrology"
WQ_BASE = "https://environment.data.gov.uk/water-quality"

WQ_HEADERS = {"Accept": "application/ld+json"}


def pick_level_measure(measures: list[dict]) -> dict | None:
    """Prefer sparser, human-scale readings (dipped/daily) over subdaily logged
    data so a browser chart doesn't choke on tens of thousands of points."""
    if not measures:
        return None
    for m in measures:
        if "dipped" in m.get("@id", ""):
            return m
    for m in measures:
        if m.get("period") == 86400:
            return m
    return measures[0]


async def fetch_level_readings(
    client: httpx.AsyncClient, station_notation: str, since: str | None = None
) -> list[dict]:
    resp = await client.get(
        f"{HYDROLOGY_BASE}/id/stations/{station_notation}/measures",
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    measures = resp.json().get("items", [])
    measure = pick_level_measure(measures)
    if measure is None:
        return []

    measure_id = measure["@id"].rsplit("/", 1)[-1]
    params: dict = {}
    if since:
        params["mineq-date"] = since[:10]

    readings: list[dict] = []
    offset = 0
    limit = 2000
    while True:
        r = await client.get(
            f"{HYDROLOGY_BASE}/id/measures/{measure_id}/readings",
            params={**params, "_limit": limit, "_offset": offset},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            break
        for it in items:
            readings.append(
                {
                    "date_time": it.get("dateTime"),
                    "value": it.get("value"),
                    "quality": it.get("quality"),
                }
            )
        offset += limit
        if len(items) < limit:
            break
        if offset >= 50000:  # safety cap
            break
    return readings


async def fetch_chemistry_observations(
    client: httpx.AsyncClient, site_notation: str, since: str | None = None
) -> list[dict]:
    params: dict = {}
    if since:
        params["dateFrom"] = since[:10]

    observations: list[dict] = []
    skip = 0
    limit = 250
    while True:
        r = await client.get(
            f"{WQ_BASE}/sampling-point/{site_notation}/observation",
            params={**params, "limit": limit, "skip": skip},
            headers=WQ_HEADERS,
        )
        r.raise_for_status()
        members = r.json().get("member", [])
        if not members:
            break
        for obs in members:
            determinand = obs.get("observedProperty") or {}
            result = obs.get("hasResult") or {}
            unit = result.get("hasUnit") or {}
            # obs "id" is .../sample/{sampleId}/observation/{determinandCode}; the
            # determinand code alone repeats across samples/dates, so keep the
            # sample id too to get a key that's actually unique per observation.
            obs_url = obs.get("id", "")
            observation_id = "/".join(obs_url.split("/sample/", 1)[-1].split("/")) if "/sample/" in obs_url else obs_url
            observations.append(
                {
                    "observation_id": observation_id,
                    "sample_date": obs.get("phenomenonTime"),
                    "determinand_code": determinand.get("notation"),
                    "determinand_label": determinand.get("prefLabel"),
                    "result_value": result.get("numericValue"),
                    "simple_result": obs.get("hasSimpleResult"),
                    "unit_label": unit.get("altLabel") or obs.get("hasUnit"),
                }
            )
        skip += limit
        if len(members) < limit:
            break
        if skip >= 20000:  # safety cap per site
            break
    return observations


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
