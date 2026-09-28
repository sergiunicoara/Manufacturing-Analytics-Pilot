# Manufacturing Analytics Pilot

A synthetic, local manufacturing decision support demo. The generator produces fictional ERP and MES style records; the analytical core reconstructs demand, nets a multi-level BOM, runs a weekly capacity and material model, and compares interventions. No real plant integration is present.

## Start

1. Set `MSSQL_SA_PASSWORD` in `.env` if changing the local default.
2. Run `docker compose up -d --build sqlserver api frontend`.
3. If the database is empty, generate and load the synthetic dataset using the scripts in `scripts/` and `backend/app/synthetic/run_generator.py` / `backend/app/loader/load_staging_to_sql.py`.
4. Open `http://localhost:5173`; the API is at `http://localhost:8000/docs`.

The first analytics request computes five 12-week reference cases and persists their material and capacity results. It can take substantially longer than later requests. Scenario Lab can run and persist custom cases. For the lead-time comparison, open `/api/executive-story`.

## Map

- `backend/app/synthetic`: deterministic data generation.
- `backend/app/dq`: data quality and blocking rules.
- `backend/app/analytics`: pure planning calculations, scenario engine, evidence, and read models.
- `backend/app/api.py`: dashboard, scenario, story, and copilot endpoints.
- `frontend/src/DashboardApp.tsx`: nine pages, Plotly charts, and Evidence Drawer.
- `db/ddl/001_schema.sql`: database schema.

Read [ARCHITECTURE.md](ARCHITECTURE.md), [ANALYTICS_METHODS.md](ANALYTICS_METHODS.md), and [DEMO_GUIDE.md](DEMO_GUIDE.md) before interpreting the charts.
