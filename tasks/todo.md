# CP1 — Foundation

- [x] Repo scaffold (backend/frontend/db/scripts dirs, .gitignore, .env.example)
- [x] Corrected DDL schema (db/ddl/001_schema.sql) — 3-tier capacity, material
      netting output tables, period_engine_results, forecast_consumption audit,
      intentionally-unenforced FK columns documented for DQ testing
- [x] Docker Compose (sqlserver + api + frontend)
- [x] Seeded synthetic ERP data generator (master data, 5-family multi-level
      BOM/routing, demand, execution, capacity/cost, state snapshots)
- [x] 13 categories of injected data-quality defects + manifest
- [x] CSV staging -> SQL Server loader (SQLAlchemy reflection, IDENTITY_INSERT)
- [x] Smoke test (row counts vs. target scale)
- [x] CP1 pytest suite (determinism, structural invariants, injected-DQ presence)
- [x] Minimal frontend scaffold, CORS fix, full docker compose up verified
- [x] git init + CP1 commit

## Next (CP2, per PLAN.md) — COMPLETE

- [x] Data Quality Engine: 20-rule registry (13 INJECTED, 7 ORGANIC), origin +
      manifest_key tagging, writes to dq_findings
- [x] BOM explosion engine (recursive, cycle detection, effective-dating,
      full lineage with source record IDs)
- [x] Material netting (CORR-4): gross requirement - usable inventory -
      scheduled receipts = net requirement / shortage, persisted to
      material_requirements under a baseline scenario_runs row
- [x] Routing analytical layer (folded into DQ rules: missing WC/times,
      invalid batch size/yield)
- [x] Unit tests: recursion, cycles, orphans, overlapping revisions,
      effective-date selection, scrap/yield propagation, netting exclusion/
      timing/shortage, DQ rule cross-check vs. manifest (29 tests total)

## Next (CP3, per PLAN.md)

- [ ] Forecast reconstruction + scoped consumption (CORR-3) + accuracy metrics
- [ ] Three-tier capacity (CORR-5) wired into a real engine
- [ ] period_engine.py: stateful, period-stepped backlog/WIP (CORR-1/2)
- [ ] Constraint classification (CORR-6)
