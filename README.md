# Groundwater Monitoring in England

**Live app:** [groundwater-monitoring-sigma.vercel.app](https://groundwater-monitoring-sigma.vercel.app)
**API:** [groundwater-monitoring-api.vercel.app](https://groundwater-monitoring-api.vercel.app)

An interactive map and analytics dashboard covering groundwater level
stations and water-quality sampling points across England, built on
Environment Agency open data. Rather than just plotting raw readings, the
backend runs statistical trend and outlier detection over each site's full
history so you can see at a glance whether a borehole is rising, falling, or
behaving erratically.

- **Backend**: FastAPI + SQLite/[Turso](https://turso.tech) (libSQL), deployed
  as a Vercel serverless function
- **Frontend**: Next.js + React-Leaflet + Recharts, deployed on Vercel
- **Data**: [EA Hydrology API](https://environment.data.gov.uk/hydrology/doc/reference)
  and [EA Water Quality Archive API](https://environment.data.gov.uk/water-quality/api-docs)

## Features

- **~9,200 monitoring sites on one map** — every groundwater level station
  and water-quality sampling point the Environment Agency publishes for
  England, colour-coded (blue = level, green = quality), with postcode and
  name search.
- **Per-site time series** — click any site for its full reading history:
  a chemistry determinand selector + chart for quality sites, a level chart
  (mAOD) for level stations.
- **Trend detection** — a Mann-Kendall test (the standard technique EA/CEH
  hydrogeologists use for borehole trend analysis) reports whether each
  site is trending up, down, or stable, with a significance p-value and a
  Sen's-slope rate of change per year, computed to handle the irregular
  sampling intervals real monitoring data has.
- **Outlier detection** — a median-absolute-deviation (modified z-score)
  method flags anomalous readings directly on the chart, robust to the
  skewed, non-detect-heavy distributions typical of water quality data.
- **Data quality flags** — each site is automatically labelled (Good,
  Limited data, Stale, Mostly non-detect) based on record count, censored
  (non-detect) fraction, and recency, so you can distinguish a genuine
  trend from an artifact of sparse or old data.
- **Nightly incremental refresh** — a GitHub Actions job re-syncs site
  registries and new readings every night and recomputes stats/trends/
  outliers, so the dataset keeps growing without a manual rebuild. Sites
  not yet reached by a nightly run are fetched live on first click.

## Architecture

```
frontend/   Next.js app — map, search, site detail panel, charts
backend/    FastAPI app — site registry, time series, stats endpoints
  app/ingest.py    bulk site-registry download (~3,600 level stations,
                   ~5,600 quality sampling points)
  app/refresh.py   nightly job: incremental sync + trend/outlier recompute
  app/stats.py     pure statistics helpers (Mann-Kendall, Sen's slope,
                   modified z-score outliers, data quality flags)
  app/ea_client.py async fetchers for the EA hydrology/water-quality APIs
```

Time-series readings are fetched from the EA APIs once per site and cached
in the database from then on; the nightly refresh job tops these up
incrementally rather than re-downloading full history each time.

## Running locally

### Backend

```bash
cd backend
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m app.ingest              # populates the site registry
venv/bin/uvicorn app.main:app --port 8000 --reload
```

By default this uses a local SQLite file (`backend/data/groundwater.db`).
To point at a Turso database instead, set `TURSO_DATABASE_URL` and
`TURSO_AUTH_TOKEN` in `backend/.env`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE=http://localhost:8000
npm run dev
```

Open http://localhost:3000.
