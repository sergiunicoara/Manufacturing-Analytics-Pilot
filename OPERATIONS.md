# Local operations

## Run and inspect

`docker compose up -d --build` starts SQL Server, FastAPI, and Vite. `docker compose ps` shows container state. `docker compose logs api` reveals API errors. The frontend uses `VITE_API_BASE_URL`, defaulting to `http://localhost:8000`.

For a database created before CP4, apply `db/ddl/002_nullable_blocked_period_results.sql` once. Fresh databases already have the nullable result fields in `001_schema.sql`. This lets KPI-blocked periods persist unknown capacity and queue values as SQL `NULL`.

The API loads all synthetic tables and computes the reference comparison on first analytics access; later requests use its process cache. Restarting the API clears that cache but reuses persisted reference runs with matching parameter JSON and seed. Changing source tables while the API is running requires an API restart for fresh read models.

## Regenerate

Run the synthetic generator with the configured seed, load staging tables into SQL Server, then restart the API. Keep seed and generated staging outputs together when comparing reports. Custom scenario runs are append-only; their parameters remain in `scenario_runs`.

## Verify

Run `PYTHONPATH=backend python -m pytest backend/tests -q` from the project root (PowerShell: `$env:PYTHONPATH='backend'; python -m pytest backend/tests -q`). Run `npm run build` in `frontend`. Check `/health`, `/kpi/plant-overview`, `/api/executive-story`, `/story`, and the dashboard in a browser.
