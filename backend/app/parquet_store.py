"""Per-station/site Parquet storage on Cloudflare R2 for the two bulk
time-series tables (level_readings, chemistry_observations) that used to live
in Turso. One file per station/site, since every read pattern in this app is
a single-station lookup (`/api/sites/level/{notation}/timeseries`), never a
cross-station scan - so there's no benefit to a partitioned/multi-file
dataset here, and a single-object GET/PUT per station is the cheapest way to
use R2.
"""
import os

import duckdb
import pyarrow as pa
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET")

LEVEL_READINGS_SCHEMA = pa.schema(
    [
        ("date_time", pa.string()),
        ("value", pa.float64()),
        ("quality", pa.string()),
        ("is_outlier", pa.int64()),
    ]
)

CHEMISTRY_OBSERVATIONS_SCHEMA = pa.schema(
    [
        ("observation_id", pa.string()),
        ("sample_date", pa.string()),
        ("determinand_code", pa.string()),
        ("determinand_label", pa.string()),
        ("result_value", pa.float64()),
        ("simple_result", pa.string()),
        ("unit_label", pa.string()),
        ("is_outlier", pa.int64()),
    ]
)


def _connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        """
        CREATE SECRET r2_secret (
            TYPE r2,
            KEY_ID ?,
            SECRET ?,
            ACCOUNT_ID ?
        )
        """,
        [R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID],
    )
    return con


def _path(site_type: str, notation: str) -> str:
    return f"r2://{R2_BUCKET}/readings/{site_type}/{notation}.parquet"


def _read(site_type: str, notation: str) -> list[dict]:
    con = _connection()
    try:
        rows = con.execute(f"SELECT * FROM read_parquet('{_path(site_type, notation)}')").fetchall()
        cols = [d[0] for d in con.description]
        return [dict(zip(cols, row)) for row in rows]
    except duckdb.IOException:
        # No object at that key yet - this station/site has never synced.
        return []
    finally:
        con.close()


def _write(site_type: str, notation: str, rows: list[dict], schema: pa.Schema) -> None:
    con = _connection()
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
        con.register("t", table)
        con.execute(f"COPY t TO '{_path(site_type, notation)}' (FORMAT PARQUET)")
    finally:
        con.close()


def read_level_readings(notation: str) -> list[dict]:
    return _read("level", notation)


def read_chemistry_observations(notation: str) -> list[dict]:
    return _read("quality", notation)


def write_level_readings(notation: str, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r["date_time"])
    _write("level", notation, rows, LEVEL_READINGS_SCHEMA)


def write_chemistry_observations(notation: str, rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: r["sample_date"] or "")
    _write("quality", notation, rows, CHEMISTRY_OBSERVATIONS_SCHEMA)


def merge_level_readings(notation: str, new_readings: list[dict]) -> list[dict]:
    """Merge freshly-fetched readings into the station's existing file,
    keyed by date_time (new values win), and persist. Returns the full
    merged list. New/changed rows get is_outlier reset to 0 - the next stats
    recompute pass (see refresh.py) recomputes outliers over the full
    history and overwrites the file again via write_level_readings."""
    existing = {r["date_time"]: r for r in read_level_readings(notation)}
    for r in new_readings:
        existing[r["date_time"]] = {
            "date_time": r["date_time"],
            "value": r["value"],
            "quality": r["quality"],
            "is_outlier": 0,
        }
    merged = list(existing.values())
    write_level_readings(notation, merged)
    return merged


def merge_chemistry_observations(notation: str, new_observations: list[dict]) -> list[dict]:
    """Same as merge_level_readings but keyed by observation_id."""
    existing = {r["observation_id"]: r for r in read_chemistry_observations(notation)}
    for o in new_observations:
        existing[o["observation_id"]] = {
            "observation_id": o["observation_id"],
            "sample_date": o["sample_date"],
            "determinand_code": o["determinand_code"],
            "determinand_label": o["determinand_label"],
            "result_value": o["result_value"],
            "simple_result": o["simple_result"],
            "unit_label": o["unit_label"],
            "is_outlier": 0,
        }
    merged = list(existing.values())
    write_chemistry_observations(notation, merged)
    return merged
