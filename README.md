# UK Groundwater Monitoring Dashboard

Interactive map and dashboard for UK groundwater level and quality monitoring
sites, built on Environment Agency open data.

- **Backend**: FastAPI + SQLite (`backend/`)
- **Frontend**: Next.js + React-Leaflet + Recharts (`frontend/`)

## Data sources

- [Hydrology API](https://environment.data.gov.uk/hydrology/doc/reference) —
  groundwater level monitoring stations and readings.
- [Water Quality Archive API](https://environment.data.gov.uk/water-quality/api-docs) —
  groundwater chemistry sampling points and observations.

Site registries (~3,600 level stations, ~5,600 quality sampling points) are
bulk-downloaded into SQLite once via the ingest script. Time-series readings
(levels/chemistry) are fetched from the EA APIs on demand per site, the first
time a site is opened, and cached in SQLite from then on.

## Setup

### Backend

```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt   # or see below
venv/bin/python -m app.ingest              # populates backend/data/groundwater.db
venv/bin/uvicorn app.main:app --port 8000 --reload
```

Dependencies: `fastapi`, `uvicorn[standard]`, `httpx`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev
```

Open http://localhost:3000.

## Features (v1)

- All UK groundwater level stations + quality sampling points on an
  interactive map (blue = level, green = quality).
- Click any site to open a detail panel with its time series:
  chemistry determinand selector + chart for quality sites, level chart
  for level stations.
- Search by borehole/site name, or by UK postcode (shows sites within 15km).
