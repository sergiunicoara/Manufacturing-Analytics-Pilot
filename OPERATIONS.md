# Local operations

## Run and inspect

`docker compose up -d --build` starts SQL Server, FastAPI, and Vite. `docker compose ps` shows container state. `docker compose logs api` reveals API errors. The frontend uses `VITE_API_BASE_URL`, defaulting to `http://localhost:8000`.

## Schema and migrations

`db/ddl/001_schema.sql` is the destructive bootstrap: it drops and recreates every table (including `cost_results` and the `schema_migrations` ledger, which it resets). `app.loader.load_staging_to_sql` then applies `db/migrations/*.sql` in name order, so a reload always ends with a current schema. The migrations are rerunnable and never drop data: `003` result table, `100` read models (`CREATE OR ALTER`), `200` database roles, `300` run data version.

For an existing database, apply anything pending with an administrator login:

```bash
docker exec -w /app mfg_pilot_api python -m app.db.migrate
```

The API's own login cannot run DDL. If the schema is behind, pages return 503 with "Database schema is out of date ... python -m app.db.migrate" instead of a SQL error. `db/ddl/002_nullable_blocked_period_results.sql` is only for databases created before CP4; fresh databases already have those columns.

The API loads all synthetic tables and computes the reference comparison on first analytics access; later requests use its process cache. Restarting the API clears that cache but reuses persisted reference runs whose parameter JSON, seed and data version (source-table fingerprint, analytics code hash and analytical settings) all match; anything else creates new runs and leaves the old ones as audit history. Changing source tables while the API is running requires an API restart for fresh read models.

## Regenerate

Run the synthetic generator with the configured seed, load staging tables into SQL Server (see the README), re-run `app.dq.run_dq_engine`, then restart the API. Keep seed and generated staging outputs together when comparing reports. Custom scenario runs are append-only; their parameters remain in `scenario_runs`.

### Refresh only the execution tables

The execution-history calibration changes only `production_orders` and `production_order_operations`. To apply a regenerated version without the destructive reload (scenario and cost history are kept):

```bash
docker exec -w /app mfg_pilot_api python -m app.synthetic.run_generator --staging-dir /app/staging
docker exec -w /app mfg_pilot_api python -m app.loader.refresh_execution --staging-dir /app/staging
docker exec -w /app mfg_pilot_api python -m app.dq.run_dq_engine
docker compose restart api
```

It refuses if any other table's row count differs from staging, or if an existing order's identity columns (order number, item, quantity, status, planned dates) changed.

## Backup, restore, security

See [docs/RESTORE_RUNBOOK.md](docs/RESTORE_RUNBOOK.md) (new database only, never overwrites) and [docs/SECURITY.md](docs/SECURITY.md) (secured profile, API keys, least-privilege logins).

## Verify

Run `PYTHONPATH=backend python -m pytest backend/tests -q` from the project root (PowerShell: `$env:PYTHONPATH='backend'; python -m pytest backend/tests -q`). Run `npm run build` in `frontend`. Check `/health`, `/kpi/plant-overview`, `/api/executive-story`, `/story`, and the dashboard in a browser.

### Dependency checks

```bash
pip install pip-audit && pip-audit -r backend/requirements.txt
cd frontend && npm audit --omit=dev
```

On 2026-09-30 `pip-audit` flagged `starlette` (via `fastapi`), `pytest` and `python-dotenv`; the pins were raised (`fastapi 0.142.2`, `starlette 1.7.0`, `pytest 9.0.3`, `python-dotenv 1.2.2`) and both audits now report nothing. Re-run them periodically; a pin is a snapshot, not a guarantee.

### Live database tests

Three test files need SQL Server: SQL/Python parity of the read models, and (with two logins) least privilege. By default they skip when the database or logins are missing, so the suite runs anywhere. Run them for real, and fail rather than skip, inside the API container:

```bash
docker exec -e REQUIRE_DB_TESTS=1 -e PILOT_READER_PASSWORD -e PILOT_APP_PASSWORD -w /app mfg_pilot_api python -m pytest -q -rs
```

Create the two logins once with `python -m app.db.security_setup`, supplying passwords through the `PILOT_READER_PASSWORD` and `PILOT_APP_PASSWORD` environment variables (from a password manager or a local ignored file; never commit them). The final line of a healthy run has no skips.

## Checkpoint report scripts

Read-only reports that print acceptance evidence for earlier checkpoints, kept for reproducibility. Run inside the API container against the loaded database:

| Command | Prints |
|---|---|
| `python -m app.analytics.run_cp3_report` | Forecast reconstruction, consumption example, accuracy by horizon, capacity tiers, golden scenarios |
| `python -m app.analytics.run_cp3_1_report` | MRP netting and calibration checks |
| `python -m app.analytics.run_cp3_2_report` | Flow-conservation identities, system KPIs, per-work-centre interventions |
| `python -m app.analytics.run_executive_story_report` | The 12-week CAB-100 lead-time series for the five cases |
| `python -m app.analytics.run_baseline_netting` | Writes its own run, "CP2 baseline material requirements"; it never touches the five reference cases |

## Local housekeeping

Scenario runs are append-only audit history and are not deleted. Disposable restore-check databases and their `.bak` files are removed by hand once validated (see the runbook). The local test logins `pilot_reader` and `pilot_app` are kept for the live database tests.
