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

## Next (CP2, per PLAN.md)

- [ ] Data Quality Engine: rule registry reading the intentionally-unenforced
      columns and injected defects, writing to dq_findings
- [ ] BOM explosion engine (recursive, cycle detection, effective-dating)
- [ ] Material netting (CORR-4): gross requirement - usable inventory -
      scheduled receipts = net requirement / shortage
- [ ] Routing analytical layer
- [ ] Unit tests: recursion, cycles, netting correctness
