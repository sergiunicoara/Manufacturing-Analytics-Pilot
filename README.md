# Manufacturing Analytics Pilot

A synthetic, local manufacturing decision support demo. The generator produces fictional ERP and MES style records; the analytical core reconstructs demand, nets a multi-level BOM, runs a weekly capacity and material model, and compares interventions. No real plant integration is present.

## Start

1. Set `MSSQL_SA_PASSWORD` in `.env` if changing the local default. SQL Server, the API and the dashboard are published on `127.0.0.1` only.
2. Run `docker compose up -d --build sqlserver api frontend`.
3. If the database is empty, generate and load the synthetic dataset inside the API container. The loader drops and recreates the schema, then applies `db/migrations` (result tables, read models, roles, run data version); the API refuses to serve pages until those are present.
   ```bash
   docker exec -w /app mfg_pilot_api python -m app.synthetic.run_generator --staging-dir /app/staging
   docker exec -w /app mfg_pilot_api python -m app.loader.load_staging_to_sql --staging-dir /app/staging
   docker exec -w /app mfg_pilot_api python -m app.dq.run_dq_engine
   docker compose restart api
   ```
4. Open `http://localhost:5173`; the API is at `http://localhost:8000/docs`. After a start the API warms its caches in the background (about 70 s); `http://localhost:8000/health` shows when it is ready.

For client data use the secured profile instead (start-up steps and what was verified are in [docs/SECURITY.md](docs/SECURITY.md)); the demo profile is for synthetic data only.

The five 12-week reference cases are computed once per API process, in the background after start, and their material and capacity results are persisted. A page requested during that warm-up waits for it; afterwards pages are immediate. Scenario Lab can run and persist custom cases. For the lead-time comparison, open `/api/executive-story`.

## Map

- `backend/app/synthetic`: deterministic data generation.
- `backend/app/dq`: data quality and blocking rules.
- `backend/app/analytics`: pure planning calculations, scenario engine, evidence, and read models.
- `backend/app/api.py`: dashboard, scenario, story, and copilot endpoints.
- `backend/app/security.py`: profile validation and API-key authorization (demo vs. secured).
- `backend/app/integration`, `backend/app/integrations`: data-request completeness checker; simulated ERP / BI / MES adapters.
- `backend/app/db`: migration runner, restore tooling, login setup.
- `frontend/src/DashboardApp.tsx`: fourteen pages, Plotly charts, and Evidence Drawer.
- `db/ddl/001_schema.sql`: base schema (destructive bootstrap). `db/migrations/`: rerunnable, versioned additions.
- `docs/`: [requirements matrix](docs/REQUIREMENTS_COMPLETION_MATRIX.md), [data request](docs/DATA_REQUEST.md), [M3 mapping](docs/M3_MAPPING.md), [integration](docs/INTEGRATION.md), [restore runbook](docs/RESTORE_RUNBOOK.md), [security](docs/SECURITY.md), [architecture page](docs/architecture.html).

Read [ARCHITECTURE.md](ARCHITECTURE.md), [ANALYTICS_METHODS.md](ANALYTICS_METHODS.md), [LIMITATIONS.md](LIMITATIONS.md) and [DEMO_GUIDE.md](DEMO_GUIDE.md) before interpreting the charts. Day-to-day commands are in [OPERATIONS.md](OPERATIONS.md).
