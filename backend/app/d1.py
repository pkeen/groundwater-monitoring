"""Thin HTTP client over Cloudflare D1's REST query endpoint, replacing the
old Turso/libsql-client. Mirrors the row-access shape the old `db.py` client
exposed (`row["col"]`, `row.asdict()`, `len(result_set)`, `await
client.execute(...)`, `await client.batch(...)`, `await client.close()`) so
call sites (`main.py`, `ingest.py`, `refresh.py`) needed minimal changes.
"""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN")
D1_DATABASE_ID = os.environ.get("D1_DATABASE_ID")

BASE_URL = "https://api.cloudflare.com/client/v4"

# D1's REST API caps how many statements can ride in one /query batch call -
# chunk larger batches (e.g. a full ~9,200-site ingest) rather than risk a
# request-size/statement-count rejection.
BATCH_CHUNK_SIZE = 100


class Row:
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def asdict(self) -> dict:
        return dict(self._data)


class ResultSet:
    def __init__(self, rows: list[dict]):
        self._rows = [Row(r) for r in rows]

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __getitem__(self, index):
        return self._rows[index]


def _url() -> str:
    return f"{BASE_URL}/accounts/{CLOUDFLARE_ACCOUNT_ID}/d1/database/{D1_DATABASE_ID}/query"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _raise_for_errors(payload: dict) -> None:
    if not payload.get("success", False):
        errors = payload.get("errors", [])
        raise RuntimeError(f"D1 query failed: {errors}")


class Client:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30.0)

    async def execute(self, sql: str, params: list | None = None) -> ResultSet:
        resp = await self._http.post(
            _url(), headers=_headers(), json={"sql": sql, "params": params or []}
        )
        resp.raise_for_status()
        payload = resp.json()
        _raise_for_errors(payload)
        result = payload["result"]
        if not result:
            return ResultSet([])
        return ResultSet(result[0].get("results", []))

    async def batch(self, statements: list[tuple[str, list]]) -> None:
        for i in range(0, len(statements), BATCH_CHUNK_SIZE):
            chunk = statements[i : i + BATCH_CHUNK_SIZE]
            body = {"batch": [{"sql": sql, "params": params or []} for sql, params in chunk]}
            resp = await self._http.post(_url(), headers=_headers(), json=body)
            resp.raise_for_status()
            _raise_for_errors(resp.json())

    async def close(self) -> None:
        await self._http.aclose()


def get_client() -> Client:
    return Client()


# Metadata/stats tables only - level_readings and chemistry_observations now
# live as per-station Parquet files on R2 (see app/parquet_store.py), not D1.
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS level_stations (
        notation TEXT PRIMARY KEY,
        label TEXT,
        lat REAL,
        lon REAL,
        easting REAL,
        northing REAL,
        station_guid TEXT,
        wiski_id TEXT,
        aquifer TEXT,
        borehole_depth REAL,
        date_opened TEXT,
        status TEXT,
        measure_notation TEXT,
        measure_parameter TEXT,
        measure_period INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS quality_sites (
        notation TEXT PRIMARY KEY,
        label TEXT,
        lat REAL,
        lon REAL,
        easting REAL,
        northing REAL,
        site_type_code TEXT,
        site_type_label TEXT,
        status_code TEXT,
        status_label TEXT,
        region_label TEXT,
        area_label TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS site_sync_state (
        site_notation TEXT,
        site_type TEXT,
        last_synced_at TEXT,
        latest_data_date TEXT,
        PRIMARY KEY (site_notation, site_type)
    )""",
    """CREATE TABLE IF NOT EXISTS quality_site_stats (
        site_notation TEXT,
        determinand_code TEXT,
        determinand_label TEXT,
        unit_label TEXT,
        count INTEGER,
        censored_count INTEGER,
        min_value REAL,
        max_value REAL,
        mean_value REAL,
        median_value REAL,
        stddev_value REAL,
        latest_value REAL,
        latest_date TEXT,
        first_date TEXT,
        trend_direction TEXT,
        trend_slope_per_year REAL,
        trend_p_value REAL,
        outlier_count INTEGER,
        data_quality_label TEXT,
        data_quality_flags TEXT,
        last_computed TEXT,
        PRIMARY KEY (site_notation, determinand_code)
    )""",
    """CREATE TABLE IF NOT EXISTS level_station_stats (
        station_notation TEXT PRIMARY KEY,
        count INTEGER,
        min_value REAL,
        max_value REAL,
        mean_value REAL,
        median_value REAL,
        stddev_value REAL,
        latest_value REAL,
        latest_date TEXT,
        first_date TEXT,
        trend_direction TEXT,
        trend_slope_per_year REAL,
        trend_p_value REAL,
        outlier_count INTEGER,
        data_quality_label TEXT,
        data_quality_flags TEXT,
        last_computed TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_level_stations_latlon ON level_stations(lat, lon)",
    "CREATE INDEX IF NOT EXISTS idx_quality_sites_latlon ON quality_sites(lat, lon)",
    "CREATE INDEX IF NOT EXISTS idx_quality_stats_site ON quality_site_stats(site_notation)",
]


async def init_db() -> None:
    client = get_client()
    try:
        for stmt in SCHEMA_STATEMENTS:
            await client.execute(stmt)
    except Exception as exc:
        print(f"init_db: schema check/migration failed, continuing anyway: {exc}")
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(init_db())
    print(f"Initialized D1 database {D1_DATABASE_ID}")
