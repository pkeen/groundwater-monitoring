"""Nightly refresh entrypoint (run by the GitHub Actions workflow, or by
hand locally: `python -m app.refresh`).

Three phases:
  1. Re-sync site registries (cheap, catches new/closed sites).
  2. Incrementally fetch new readings/observations per site, since whatever
     `site_sync_state.latest_data_date` says (or the last `HISTORY_CAP_YEARS`
     on first run), with bounded concurrency so a full ~9,200-site backfill
     is tractable.
  3. Recompute summary stats/trend/outlier flags, but only for sites that
     actually received new readings/observations this run - a site with no
     new data has an unchanged series, so its cached stats are still
     correct. This matters because Turso bills UPDATEs by rows affected:
     recomputing a site rewrites `is_outlier` across its *entire* reading
     history, so doing this unconditionally for every ever-synced site,
     every night, silently burns through the row-write quota regardless of
     how much new data actually arrived.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import stats
from app.db import get_client, init_db
from app.ea_client import fetch_chemistry_observations, fetch_level_readings, history_cutoff_date, now_iso
from app.ingest import ingest_level_stations, ingest_quality_sites

CONCURRENCY = 10


async def get_sync_state(db, notation: str, site_type: str) -> dict | None:
    rs = await db.execute(
        "SELECT * FROM site_sync_state WHERE site_notation = ? AND site_type = ?",
        [notation, site_type],
    )
    return rs[0].asdict() if len(rs) else None


async def set_sync_state(db, notation: str, site_type: str, latest_data_date: str | None) -> None:
    await db.execute(
        """INSERT INTO site_sync_state (site_notation, site_type, last_synced_at, latest_data_date)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (site_notation, site_type) DO UPDATE SET
             last_synced_at = excluded.last_synced_at,
             latest_data_date = COALESCE(excluded.latest_data_date, site_sync_state.latest_data_date)""",
        [notation, site_type, now_iso(), latest_data_date],
    )


async def sync_level_station(sem: asyncio.Semaphore, http_client: httpx.AsyncClient, notation: str) -> int:
    async with sem:
        db = get_client()
        try:
            state = await get_sync_state(db, notation, "level")
            since = state["latest_data_date"] if state else history_cutoff_date()
            readings = await fetch_level_readings(http_client, notation, since=since)
            if readings:
                statements = [
                    (
                        "INSERT OR REPLACE INTO level_readings (station_notation, date_time, value, quality) VALUES (?,?,?,?)",
                        [notation, r["date_time"], r["value"], r["quality"]],
                    )
                    for r in readings
                ]
                await db.batch(statements)
                latest = max(r["date_time"] for r in readings if r["date_time"])
                await set_sync_state(db, notation, "level", latest)
            else:
                await set_sync_state(db, notation, "level", None)
            return len(readings)
        except Exception as exc:
            print(f"  [level:{notation}] error: {exc}")
            return 0
        finally:
            await db.close()


async def sync_quality_site(sem: asyncio.Semaphore, http_client: httpx.AsyncClient, notation: str) -> int:
    async with sem:
        db = get_client()
        try:
            state = await get_sync_state(db, notation, "quality")
            since = state["latest_data_date"] if state else history_cutoff_date()
            observations = await fetch_chemistry_observations(http_client, notation, since=since)
            if observations:
                statements = [
                    (
                        """INSERT OR REPLACE INTO chemistry_observations
                           (site_notation, observation_id, sample_date, determinand_code, determinand_label,
                            result_value, simple_result, unit_label)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        [
                            notation,
                            o["observation_id"],
                            o["sample_date"],
                            o["determinand_code"],
                            o["determinand_label"],
                            o["result_value"],
                            o["simple_result"],
                            o["unit_label"],
                        ],
                    )
                    for o in observations
                ]
                await db.batch(statements)
                latest = max(o["sample_date"] for o in observations if o["sample_date"])
                await set_sync_state(db, notation, "quality", latest)
            else:
                await set_sync_state(db, notation, "quality", None)
            return len(observations)
        except Exception as exc:
            print(f"  [quality:{notation}] error: {exc}")
            return 0
        finally:
            await db.close()


async def _sync_level_task(sem: asyncio.Semaphore, http_client: httpx.AsyncClient, notation: str) -> tuple[str, str, int]:
    count = await sync_level_station(sem, http_client, notation)
    return "level", notation, count


async def _sync_quality_task(sem: asyncio.Semaphore, http_client: httpx.AsyncClient, notation: str) -> tuple[str, str, int]:
    count = await sync_quality_site(sem, http_client, notation)
    return "quality", notation, count


async def sync_all_readings() -> tuple[set[str], set[str]]:
    db = get_client()
    level_notations = [r["notation"] for r in await db.execute("SELECT notation FROM level_stations")]
    quality_notations = [r["notation"] for r in await db.execute("SELECT notation FROM quality_sites")]
    await db.close()

    sem = asyncio.Semaphore(CONCURRENCY)
    updated_level: set[str] = set()
    updated_quality: set[str] = set()
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        tasks = [_sync_level_task(sem, http_client, n) for n in level_notations]
        tasks += [_sync_quality_task(sem, http_client, n) for n in quality_notations]

        total = len(tasks)
        done = 0
        new_rows = 0
        for coro in asyncio.as_completed(tasks):
            site_type, notation, count = await coro
            new_rows += count
            if count > 0:
                (updated_level if site_type == "level" else updated_quality).add(notation)
            done += 1
            if done % 200 == 0 or done == total:
                print(f"  synced {done}/{total} sites ({new_rows} new rows so far)...")

    return updated_level, updated_quality


async def recompute_level_stats(db, notation: str) -> None:
    rs = await db.execute(
        "SELECT date_time, value, is_outlier FROM level_readings WHERE station_notation = ? AND value IS NOT NULL ORDER BY date_time",
        [notation],
    )
    rows = [r.asdict() for r in rs]
    if not rows:
        return
    dates = [r["date_time"] for r in rows]
    values = [r["value"] for r in rows]
    stored_outlier = [bool(r["is_outlier"]) for r in rows]

    summary = stats.summarize(values)
    trend = stats.trend(dates, values)
    outlier_flags = stats.detect_outliers(values)
    quality = stats.data_quality_flags(
        count=len(rows),
        censored_count=0,
        latest_date=dates[-1],
        stale_days_threshold=stats.STALE_DAYS_LEVEL,
    )

    statements = [
        (
            """INSERT INTO level_station_stats
               (station_notation, count, min_value, max_value, mean_value, median_value, stddev_value,
                latest_value, latest_date, first_date, trend_direction, trend_slope_per_year, trend_p_value,
                outlier_count, data_quality_label, data_quality_flags, last_computed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (station_notation) DO UPDATE SET
                 count=excluded.count, min_value=excluded.min_value, max_value=excluded.max_value,
                 mean_value=excluded.mean_value, median_value=excluded.median_value, stddev_value=excluded.stddev_value,
                 latest_value=excluded.latest_value, latest_date=excluded.latest_date, first_date=excluded.first_date,
                 trend_direction=excluded.trend_direction, trend_slope_per_year=excluded.trend_slope_per_year,
                 trend_p_value=excluded.trend_p_value, outlier_count=excluded.outlier_count,
                 data_quality_label=excluded.data_quality_label, data_quality_flags=excluded.data_quality_flags,
                 last_computed=excluded.last_computed""",
            [
                notation, summary["count"], summary["min_value"], summary["max_value"], summary["mean_value"],
                summary["median_value"], summary["stddev_value"], values[-1], dates[-1], dates[0],
                trend["trend_direction"], trend["trend_slope_per_year"], trend["trend_p_value"],
                sum(outlier_flags), quality["label"], json.dumps(quality), now_iso(),
            ],
        ),
    ]
    statements += [
        (
            "UPDATE level_readings SET is_outlier = ? WHERE station_notation = ? AND date_time = ?",
            [int(flagged), notation, d],
        )
        for d, flagged, was_flagged in zip(dates, outlier_flags, stored_outlier)
        if flagged != was_flagged
    ]
    await db.batch(statements)


async def recompute_quality_stats(db, notation: str) -> None:
    rs = await db.execute(
        """SELECT observation_id, sample_date, determinand_code, determinand_label, result_value, simple_result, unit_label, is_outlier
           FROM chemistry_observations WHERE site_notation = ? ORDER BY sample_date""",
        [notation],
    )
    rows = [r.asdict() for r in rs]
    if not rows:
        return

    by_determinand: dict[str, list[dict]] = {}
    for r in rows:
        by_determinand.setdefault(r["determinand_code"], []).append(r)

    statements = []
    for code, obs in by_determinand.items():
        label = obs[0]["determinand_label"]
        unit = obs[0]["unit_label"]
        numeric = [o for o in obs if o["result_value"] is not None]
        dates = [o["sample_date"] for o in numeric]
        values = [o["result_value"] for o in numeric]
        censored_count = sum(1 for o in obs if o["result_value"] is None and o["simple_result"])

        summary = stats.summarize(values)
        trend = stats.trend(dates, values) if values else {
            "trend_direction": "insufficient_data", "trend_slope_per_year": None, "trend_p_value": None,
        }
        outlier_flags = stats.detect_outliers(values) if values else []
        quality = stats.data_quality_flags(
            count=len(obs),
            censored_count=censored_count,
            latest_date=obs[-1]["sample_date"],
            stale_days_threshold=stats.STALE_DAYS_QUALITY,
        )

        statements.append((
            """INSERT INTO quality_site_stats
               (site_notation, determinand_code, determinand_label, unit_label, count, censored_count,
                min_value, max_value, mean_value, median_value, stddev_value, latest_value, latest_date,
                first_date, trend_direction, trend_slope_per_year, trend_p_value, outlier_count,
                data_quality_label, data_quality_flags, last_computed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (site_notation, determinand_code) DO UPDATE SET
                 determinand_label=excluded.determinand_label, unit_label=excluded.unit_label,
                 count=excluded.count, censored_count=excluded.censored_count,
                 min_value=excluded.min_value, max_value=excluded.max_value, mean_value=excluded.mean_value,
                 median_value=excluded.median_value, stddev_value=excluded.stddev_value,
                 latest_value=excluded.latest_value, latest_date=excluded.latest_date, first_date=excluded.first_date,
                 trend_direction=excluded.trend_direction, trend_slope_per_year=excluded.trend_slope_per_year,
                 trend_p_value=excluded.trend_p_value, outlier_count=excluded.outlier_count,
                 data_quality_label=excluded.data_quality_label, data_quality_flags=excluded.data_quality_flags,
                 last_computed=excluded.last_computed""",
            [
                notation, code, label, unit, summary["count"], censored_count, summary["min_value"],
                summary["max_value"], summary["mean_value"], summary["median_value"], summary["stddev_value"],
                values[-1] if values else None, obs[-1]["sample_date"], obs[0]["sample_date"],
                trend["trend_direction"], trend["trend_slope_per_year"], trend["trend_p_value"],
                sum(outlier_flags), quality["label"], json.dumps(quality), now_iso(),
            ],
        ))

        for o, flagged in zip(numeric, outlier_flags):
            if flagged != bool(o["is_outlier"]):
                statements.append((
                    "UPDATE chemistry_observations SET is_outlier = ? WHERE site_notation = ? AND observation_id = ?",
                    [int(flagged), notation, o["observation_id"]],
                ))

    await db.batch(statements)


async def recompute_all_stats(updated_level: set[str], updated_quality: set[str]) -> None:
    """Only recomputes sites that received new readings/observations this
    run - see the module docstring for why recomputing untouched sites is
    both wasted work and a quota hazard."""
    db = get_client()
    try:
        level_notations = sorted(updated_level)
        quality_notations = sorted(updated_quality)

        for i, notation in enumerate(level_notations):
            await recompute_level_stats(db, notation)
            if (i + 1) % 200 == 0:
                print(f"  recomputed level stats {i + 1}/{len(level_notations)}...")

        for i, notation in enumerate(quality_notations):
            await recompute_quality_stats(db, notation)
            if (i + 1) % 200 == 0:
                print(f"  recomputed quality stats {i + 1}/{len(quality_notations)}...")
    finally:
        await db.close()


async def main() -> None:
    start = time.monotonic()
    await init_db()

    print("Phase 1: syncing site registries...")
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        n_level = await ingest_level_stations(http_client)
        n_quality = await ingest_quality_sites(http_client)
    print(f"  {n_level} level stations, {n_quality} quality sites.\n")

    print("Phase 2: syncing readings/observations (incremental)...")
    updated_level, updated_quality = await sync_all_readings()
    print()

    print("Phase 3: recomputing stats/trend/outliers for sites with new data...")
    await recompute_all_stats(updated_level, updated_quality)

    elapsed = time.monotonic() - start
    print(f"\nRefresh complete in {elapsed / 60:.1f} minutes.")


if __name__ == "__main__":
    asyncio.run(main())
