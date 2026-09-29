# Requirements completion matrix

Baseline review: 2026-09-29, working tree on `master` at `8894825` plus uncommitted film work (not modified by this review).
Baseline at review start: 95 passed. **Final gate (2026-09-29, 06:45–06:47Z, after the last code change): `docker exec -w /app mfg_pilot_api python -m pytest -q -rs` → 182 passed, 0 skipped in 156 s** (includes live SQL parity and least-privilege tests); `npm run build` succeeded; browser check: all 12 pages and the Executive Story rendered with no errors, a custom scenario saved as run #14 (212.19 backlog h), the copilot answered a cost question with the `get_cost` tool and refused a nonexistent item. Checkpoint labels and earlier test counts in `tasks/todo.md` are history, not evidence.

**Sources.** Group A: `PLAN.md`. Group B: analytical corrections approved during the build (period-entry lead time, BOM-path lead time, 12-week presentation horizon). Group C: additional ERP-pilot requirements from `docs/IMPLEMENT_REMAINING_PROMPT.md`. The original job description is only available as a paste in a previous session transcript; it is summarised there and not quoted here.

**Status values.** `DONE` = implemented, with the verification shown · `PARTIAL` = some implemented, gap stated · `MISSING` = not implemented · `SUPERSEDED` = replaced by a later approved correction · `EXTERNAL` = needs client systems or approvals.

## A. PLAN.md requirements

| # | Requirement | Implementation | Verification | Status | Gap / limitation |
|---|---|---|---|---|---|
| A1 | Seeded synthetic ERP generator at target scale | `backend/app/synthetic/*` | `test_synthetic_data_generation.py`, `scripts/smoke_test.py` | DONE | Synthetic data only |
| A2 | Staging → SQL Server loader with preserved IDs | `backend/app/loader/load_staging_to_sql.py` | loader run + smoke test | DONE | `001_schema.sql` begins with `DROP TABLE` statements, so it is destructive when re-run (see C7) |
| A3 | Corrected schema (3-tier capacity, engine output tables) | `db/ddl/001_schema.sql`, `002_…sql` | load + tests | DONE | No migration versioning table |
| A4 | Mock `integrations/` Protocols: `ERPAdapter`, `BIExportAdapter`, `MESAdapter` | `backend/app/integrations/` (protocols + simulated file adapters) | `test_integrations.py` (5) | DONE | Simulated only |
| A5 | DQ rule registry, injected + organic, 3-way classification | `backend/app/dq/*` (20 rules) | `test_dq_rules.py`, planted→found cross-check | DONE | Explanation of counts by rule, origin, scope and coverage is missing (C9) |
| A6 | BOM explosion: effective dating, cycles, orphans, scrap | `analytics/bom.py` | `test_bom_explosion.py` | DONE | |
| A7 | Material netting (CORR-4), DQ-excluded inventory | `analytics/netting.py`, `mrp.py` | `test_netting.py`, `test_mrp_netting.py` | DONE | |
| A8 | Three-tier capacity (CORR-5) | `analytics/capacity.py` | `test_capacity.py` | DONE | |
| A9 | Piecewise queue formula (CORR-1) | superseded | — | SUPERSEDED | Replaced by the B1 continuous entry-backlog model; keep only the deviation note |
| A10 | Stateful period engine (CORR-2/7) | `analytics/period_engine.py` | `test_period_engine.py`, `test_reconciliation.py` | DONE | |
| A11 | WIP from engine; Little's Law check; ageing from `wip.stage_entered_at` | engine `wip_qty`; `stage_history.wip_ageing`; WIP & Lead Time page | `test_period_engine.py`, `test_stage_history.py` | DONE | Synthetic snapshots |
| A12 | Forecast reconstruction, MAE/WAPE/bias/volatility per horizon | `analytics/forecast.py` | `test_forecast.py` | DONE | MAPE deliberately not a headline metric (documented) |
| A13 | Scoped consumption ±4 weeks (CORR-3), non-double-counting | `forecast.consume_forecast`, `CONSUMPTION_POLICY`; audit written to `forecast_consumption` (4,213 rows live) | `test_forecast.py` (nearest bucket, no spillover, isolation) | DONE | |
| A14 | Constraint classification (CORR-6) with tie-break | `analytics/constraint.py` | `test_period_engine.py` | DONE | |
| A15 | Constraint-gated analytical buffer recommendation | `analytics/buffers.py` | `test_cp4_cp5.py` | DONE | Policy selection is not separated from sizing (C5) |
| A16 | Cost: unit cost, WIP carrying cost over the weekly series | `analytics/cost.py`, Decision Economics page, `cost_results` | `test_cost.py` (7), live five cases | DONE | WIP carrying cost reported UNAVAILABLE (mixed-item WIP); rates ASSUMED |
| A17 | Scenario engine: 4 intervention types + persistence (CORR-7/8) | `scenario_demo.py`, `scenario_store.py`, `dashboard.py` | `test_interventions.py`, `test_golden_scenarios.py`, `test_data_version.py` | DONE | Reuse now requires matching data version (runs 9–13 live) |
| A18 | Evidence object on every KPI (CORR-9) | `analytics/evidence.py` (+ units, time_scope, coverage, exclusions, data_origin on new results) | `test_cp4_cp5.py::test_every_page_supplies_evidence` | DONE | Older pages do not all carry the optional fields |
| A19 | Evidence Drawer on all 9 pages | `frontend/src/DashboardApp.tsx` | browser check (CP4) | DONE | |
| A20 | Copilot with insufficient-evidence gate (CORR-10) | `api.py:190` | `test_cp4_cp5.py` | DONE | No authorization gate for sensitive-data profile (C8) |
| A21 | Executive Story with live numbers | `/story`, `/api/executive-story` | `test_golden_scenarios.py` | DONE | |
| A22 | Reproducibility within tolerance (CORR-11) | tests | `test_synthetic_data_generation.py` | DONE | |
| A23 | SQL tests: referential integrity, effective-date overlap | DQ rules; completeness checker relationships; SQL views | `test_sql_read_models.py` (live), `test_completeness.py` | DONE | |
| A24 | 10 docs + PILOT_FINDINGS from actual results | root `*.md`, `docs/` | review | DONE | Scenario figures re-verified against the running API on 2026-09-29 |

## B. Approved analytical corrections

| # | Correction | Implementation | Verification | Status |
|---|---|---|---|---|
| B1 | Lead time from entry backlog ÷ effective h/workday → calendar days; lagged response | `analytics/leadtime.py`, `period_engine.py` | `test_period_engine.py::test_lead_time_uses_entry_backlog…`, `::test_capacity_changes_queue_not_intrinsic_processing_time` | DONE |
| B2 | FG route + longest manufactured BOM branch | `analytics/leadtime.py` | `test_period_engine.py::test_lead_time_follows_routed_subassembly…`, `test_golden_scenarios.py` | DONE |
| B3 | 12-week presentation horizon; baseline deterioration shown; 26-week engine | `dashboard.HORIZON`, `ASSUMPTIONS.md` | `test_golden_scenarios.py` | DONE |
| B4 | Five reference cases on one engine | `main.py` executive story | `test_golden_scenarios.py` | DONE |

## C. Additional ERP-pilot requirements

| # | Requirement | Status | Notes |
|---|---|---|---|
| C3 | Historical stage performance from actual operation timestamps | DONE | `stage_history.py`, Stage Performance page + records endpoint, `vw_stage_elapsed*`; 5,408 valid observations, 95.5 % coverage; recorded ≈ 10× routing standard (synthetic) |
| C4 | Cost and decision economics | DONE | Unit cost, buffer capital, carrying, added-hours cost, five-case deltas, persisted; no ROI claimed |
| C5 | Planning policy + decoupling candidates + versioned parameter export | DONE | 127/140 FG INSUFFICIENT_EVIDENCE at default thresholds (honest outcome); all M3 fields UNMAPPED |
| C6 | DATA_REQUEST.md + checker; M3 mapping; adapters; INTEGRATION.md | DONE locally / EXTERNAL | Checker live: WARN 89/5/0. M3 names remain HYPOTHESIS until client schema or vendor docs (EXTERNAL) |
| C7 | T-SQL read models + parity; restore runbook + round trip | DONE | 6 views, parity passed; round trip into `pilot_restore_check_20260929`: 30 tables, 87,205 rows, 6 views, 40 FKs identical; overwrite refused |
| C8 | Secured profile, API auth, least privilege, SECURITY.md, LLM opt-in | DONE locally / EXTERNAL | Live secured instance verified (401/403/200); pilot_reader / pilot_app grants verified; EU hosting, TLS, TDE, DPA are EXTERNAL |
| C9 | Reporting fixes: 2.5× wording, DQ explanation + full export, consumption policy evidence, data-version cache identity, orphan process inspection | DONE | 2.5× reworded in PLAN/LIMITATIONS as a historical 16-week figure; DQ grouped with unique-record coverage and CSV export; consumption policy tested; run reuse keyed on data version; report process had exited on its own (`docker top`) |
| C10 | UI/API wiring, end-to-end verification, updated docs | DONE | See the final gate above. Docs updated: ANALYTICS_METHODS, API, LIMITATIONS, PILOT_FINDINGS, DEMO_GUIDE, PLAN |
| C11 | Film | OUT OF SCOPE | Excluded by the user on 2026-09-29; the uncommitted `docs/demo_film/` files were left untouched |
