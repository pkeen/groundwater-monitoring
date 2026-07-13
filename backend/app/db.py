import os
from pathlib import Path

import libsql_client
from dotenv import load_dotenv

load_dotenv()

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "groundwater.db"

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN")

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
    """CREATE TABLE IF NOT EXISTS level_readings (
        station_notation TEXT,
        date_time TEXT,
        value REAL,
        quality TEXT,
        is_outlier INTEGER DEFAULT 0,
        PRIMARY KEY (station_notation, date_time)
    )""",
    """CREATE TABLE IF NOT EXISTS chemistry_observations (
        site_notation TEXT,
        observation_id TEXT,
        sample_date TEXT,
        determinand_code TEXT,
        determinand_label TEXT,
        result_value REAL,
        simple_result TEXT,
        unit_label TEXT,
        is_outlier INTEGER DEFAULT 0,
        PRIMARY KEY (site_notation, observation_id)
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
    "CREATE INDEX IF NOT EXISTS idx_chemistry_site_date ON chemistry_observations(site_notation, sample_date)",
    "CREATE INDEX IF NOT EXISTS idx_level_readings_station_date ON level_readings(station_notation, date_time)",
    "CREATE INDEX IF NOT EXISTS idx_quality_stats_site ON quality_site_stats(site_notation)",
]

# Columns that may be missing on a database created before this table/column
# existed. (table, column, add-column-sql-fragment)
COLUMN_MIGRATIONS = [
    ("level_readings", "is_outlier", "INTEGER DEFAULT 0"),
    ("chemistry_observations", "is_outlier", "INTEGER DEFAULT 0"),
]


def get_client() -> libsql_client.Client:
    if TURSO_DATABASE_URL:
        # The libsql:// (websocket/hrana) scheme fails its handshake against
        # Turso with this client version; the plain HTTP client works
        # reliably and suits short-lived serverless invocations better anyway.
        url = TURSO_DATABASE_URL.replace("libsql://", "https://", 1)
        return libsql_client.create_client(url, auth_token=TURSO_AUTH_TOKEN)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return libsql_client.create_client(f"file:{DB_PATH}")


async def init_db() -> None:
    """Runs schema creation/migration statements. These are idempotent
    no-ops once the schema exists, but they're still writes - if the
    database is out of write quota (or otherwise unreachable for writes),
    they'll fail, and libsql_client raises an opaque KeyError rather than a
    typed exception in that case. Since the schema is already in place on
    every deploy after the first, failing to (re)confirm that on a cold
    start shouldn't take the whole app down - log and continue so reads
    still work."""
    client = get_client()
    try:
        for stmt in SCHEMA_STATEMENTS:
            await client.execute(stmt)
        await _migrate_columns(client)
    except Exception as exc:
        print(f"init_db: schema check/migration failed, continuing anyway: {exc}")
    finally:
        await client.close()


async def _migrate_columns(client: libsql_client.Client) -> None:
    for table, column, definition in COLUMN_MIGRATIONS:
        rs = await client.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in rs}
        if column not in existing:
            await client.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def rows_to_dicts(rs: libsql_client.ResultSet) -> list[dict]:
    return [row.asdict() for row in rs]


if __name__ == "__main__":
    import asyncio

    asyncio.run(init_db())
    print(f"Initialized database at {TURSO_DATABASE_URL or DB_PATH}")
