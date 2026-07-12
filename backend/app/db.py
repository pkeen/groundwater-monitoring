import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "groundwater.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS level_stations (
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
);

CREATE TABLE IF NOT EXISTS quality_sites (
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
);

CREATE TABLE IF NOT EXISTS level_readings (
    station_notation TEXT,
    date_time TEXT,
    value REAL,
    quality TEXT,
    PRIMARY KEY (station_notation, date_time)
);

CREATE TABLE IF NOT EXISTS chemistry_observations (
    site_notation TEXT,
    observation_id TEXT,
    sample_date TEXT,
    determinand_code TEXT,
    determinand_label TEXT,
    result_value REAL,
    simple_result TEXT,
    unit_label TEXT,
    PRIMARY KEY (site_notation, observation_id)
);

CREATE TABLE IF NOT EXISTS chemistry_fetch_log (
    site_notation TEXT PRIMARY KEY,
    fetched_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_level_stations_latlon ON level_stations(lat, lon);
CREATE INDEX IF NOT EXISTS idx_quality_sites_latlon ON quality_sites(lat, lon);
CREATE INDEX IF NOT EXISTS idx_chemistry_site_date ON chemistry_observations(site_notation, sample_date);
CREATE INDEX IF NOT EXISTS idx_level_readings_station_date ON level_readings(station_notation, date_time);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
