# Requirements completion matrix

Baseline review: 2026-09-29, working tree on `master` at `8894825` plus uncommitted film work (not modified by this review).
Baseline at review start: 95 passed. **Final verification (2026-09-30, commit `ca6f083`, nothing changed since): one clean full run in the container with `REQUIRE_DB_TESTS=1` and the login passwords exported → 209 passed, 0 skipped (17:20–17:33Z); `npm run build` and `npm audit` clean; browser pass: all 12 pages, the Executive Story (4 beats, 6 charts), the evidence drawer (units, coverage, SYNTHETIC label) and the unabridged DQ export (641 rows) all work; the real secured compose override was started and probed end to end (see docs/SECURITY.md).** Previous runs: 209 passed after the cold-start fix (the first attempt omitted the login passwords, so two tests failed for that reason only and were re-run), Executive Story 231 s to 1.4 s with identical numbers. Earlier verification (2026-09-30, after the audit fixes, on a freshly rebuilt Docker stack with the README's setup steps): full suite in the container with `REQUIRE_DB_TESTS=1` → 201 passed, 0 skipped, plus one new test whose path bug was fixed and re-run (11/11 in `test_loaders.py`); the same suite on the host with the raised dependency pins → 195 passed, 5 database tests skipped (no ODBC driver); `pip-audit` and `npm audit` report no known vulnerabilities; all 12 pages and the Executive Story return 200 and render; the five scenario backlogs are unchanged (74.4 / 212.2 / 93.1 / 164.4 / 77.3).** Earlier full runs: 189 passed (2026-09-29, after the history calibration), 182 passed (2026-09-29, before it).

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
| A19 | Evidence Drawer on all pages (now 12) | `frontend/src/DashboardApp.tsx` | browser check (CP4) | DONE | |
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

## D. Audit findings (2026-09-30) and their fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| D1 | A fresh install or a reload left the schema without the migrations, or failed on the `cost_results` foreign key | `001_schema.sql` drops `cost_results` and resets `schema_migrations`; the loader applies `db/migrations` after the DDL; the API returns an actionable "schema out of date" message | Fresh install from an empty stack by the README steps; destructive reload of a populated, migrated database succeeded; `tests/test_loaders.py` checks drop order statically |
| D2 | Data version ignored `config.py` constants | `cfg:` component hashes the analytical settings | `test_data_version.py` |
| D3 | Demo ports published on all interfaces | Bound to `127.0.0.1` | `docker ps` shows `127.0.0.1:` for 1433, 8000, 5173 |
| D4 | Docs stale (nine pages, migrations, cost tables, secured profile, resolution wording) | README, OPERATIONS, ARCHITECTURE, DATA_MODEL, API, ANALYTICS_METHODS, LIMITATIONS, SECURITY updated | Review |
| D5 | `refresh_execution` and the loader had no tests | Guard logic split into pure functions; `test_loaders.py` (11 tests) | Passing in repo and container layouts |
| D6 | Legacy checkpoint report scripts undocumented | Documented (all read-only except the netting script's own run) in OPERATIONS.md | Import check |
| D7 | Database tests could skip silently | `REQUIRE_DB_TESTS=1` turns a skip into a failure | Strict run: 0 skipped |
| D8 | Local leftovers | Disposable restore database and `.bak` removed; scenario runs kept as audit history; test logins kept for the live tests | Verified absent |
| D10 | Cold start: first page about 50 s, Executive Story about 231 s | `LeadTimeCalculator` (indexed routing/BOM lookups, shared across scenarios); background warm-up at start; `/health` reports progress | Story 231 s to 1.4 s; all five cases' weekly series and KPIs identical to the earlier values; `test_leadtime_equivalence.py` compares more than 1,000 (item, week, scenario) results against a frozen copy of the old code |
| D9 | Vulnerable pins (`starlette` via `fastapi`, `pytest`, `python-dotenv`) | Raised to `fastapi 0.142.2`, `starlette 1.7.0`, `pytest 9.0.3`, `python-dotenv 1.2.2` | `pip-audit` clean; full suite passes on the new pins |

## E. Code-review findings (2026-09-30) and their fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| E1 | Backup used `INIT` and could overwrite an existing `.bak` | Refuses an existing file (`sys.dm_os_file_exists`); `COPY_ONLY, NOINIT, CHECKSUM` | Live: backup onto an existing file refused, file intact; unit tests |
| E2 | `/docs`, `/redoc`, `/openapi.json` unauthenticated in the secured profile | Disabled when `APP_PROFILE=secured` | Live: 404 with and without a key; demo still 200 |
| E3 | Copilot DQ lookup matched bare record ids across entities | `finding_concerns_item` scopes by entity | Unit test |
| E4 | Table fingerprint depended on row order with ties | Per-row hashes, sorted | Unit test |
| E5 | `security_setup` truncated long passwords and could not rotate | `nvarchar(max)`, 128-char validation, create-or-rotate | Live: rotated; 129 characters rejected |
| E6 | RESTORE MOVE names collided for several files | Named by `FileId`; FILESTREAM/full-text refused | Live restore round trip (100,625 rows, no mismatches) |
| E7 | LLM payload minimisation was a deny-list | Allow-list `LLM_ALLOWED_KEYS` | Unit test |
| E8 | Report scripts rebuilt the lead-time index per call | One `LeadTimeCalculator` per table set | Existing equivalence tests |
| E9 | DQ findings recomputed six times per dashboard build | `findings_for(tables)` cache | Unit test |
| E10 | Buffer row crashed without a computed range | Blocked row, confidence `NONE` | Unit test |

Final strict run (`REQUIRE_DB_TESTS=1`, secured logins exported): 219 passed, 0 skipped.

## F. UI tests

`frontend`: `npm test` runs 67 Vitest tests (`DashboardApp.test.tsx`, `ExecutiveStoryVisuals.test.tsx`) against a mocked API and mocked Plotly. They cover loading, API errors, stale responses, all twelve pages, the evidence drawer, chart click-through, the scenario lab, stage-record filters and paging, the copilot and the executive story. Deliberately breaking three behaviours (stale-response guard, pager step, drawer reset on navigation) made the matching tests fail. Not covered: real Plotly rendering, styling and a real browser.

`npm run test:e2e` (Playwright, system Chrome, against the running stack; read-only) adds a 5-test browser smoke test: every page loads without console errors, a chart renders and a metric opens its evidence, the executive story, stage-record filtering, and the copilot. It found two real defects that are now fixed: a missing favicon (404 in every page load) and a sidebar that could not scroll, which hid the Executive Story link on screens shorter than about 780 px.

## G. Second review round (2026-09-30) and its fixes

| # | Finding | Fix | Verification |
|---|---|---|---|
| G1 | A receipt in a zero-demand week disappeared | Receipts for items not netted that week join the carried inventory | `test_review_fixes_2.py`; the five reference backlogs are unchanged (golden tests) |
| G2 | Receipts after the horizon were credited to the final week | Receipts dated a week or more past the last period are dropped | Unit tests |
| G3 | Missing capacity data gave an optimistic finite lead time | A centre with no usable capacity period makes the route unavailable (`None`) | Unit tests |
| G4 | Capacity load ignored routing effective dates | `compute_required_hours_by_work_centre(..., period_start_date)` uses the revision effective that week | Unit test |
| G5 | Policy mixed expired and current BOM revisions | Only the revision effective on the latest order date counts | Unit test |
| G6 | A cyclic BOM got a confident policy | INSUFFICIENT_EVIDENCE with a cycle blocker | Unit tests |
| G7 | Secured bootstrap could not run on a fresh instance | `security_setup` takes `MSSQL_ADMIN_USER/PASSWORD` explicitly; docs run it before switching the API to the secured profile | Unit test; docs rewritten |
| G8 | A BLOCK completeness report exited 0; the destructive loader never checked | CLI exits 1 on BLOCK; the loader runs the check first and aborts before dropping anything (`--skip-completeness` to override) | Unit tests; synthetic data gives WARN, so the demo load is unaffected |
| G9 | BI export could pair new data with an old sidecar | Both files are prepared as temporary files and swapped in only afterwards | Unit test. Only the two final renames remain as a window |
| G10 | Executive Story crashed on a null lead time | Null shows "n/a" | UI test |
| G11 | Stage-record filters could show stale rows after a failed reload | The table is cleared when the reload fails | UI test |
| G12 | Rebuilding the film removed the burned-in captions | `finish_movie.py` burns the SRT into the canonical MP4s and keeps the selectable encodes; `build_film.py` writes `*_draft_*` files, never the canonical names | Burn-in command tested on a short clip; the full film was not rebuilt. The film stays uncommitted and outside the requirements |

Full run after these fixes: backend 233 passed (strict, none skipped), UI 65, browser 5.

## H. Third round (2026-10-01): known gaps closed

| # | Gap | Fix | Verification |
|---|---|---|---|
| H1 | Blocked stock was overwritten by that week's receipts | Blocked stock is carried forward untouched (still unusable) | `test_review_fixes_3.py`; in the reference cases inventory rises by about 1,290 units from 2026-07-27, and backlogs are unchanged |
| H2 | Past-due open receipts were dropped | Available from the first modelled week (standard MRP treatment) | Unit tests; one real line (item 44, 337 units) now covers its week-1 need of 11.82; backlogs unchanged |
| H3 | Overlapping routing revisions: capacity took the latest, lead time said unavailable | Capacity reports their operations as excluded; both now say "unknown" | Unit test (no overlaps in the synthetic data) |
| H4 | BI CSV and sidecar could not be swapped atomically | The sidecar records the CSV's SHA-256; `FileBIExportAdapter.verify(name)` detects any mismatch, including a crash between the two renames | Unit tests |
| H5 | The five-case build took about 40 s | Engine tables indexed once per run (`BomExploder`, `RoutingLoad`, `CapacityCalendar`, scalar netting) | 39.4 s to 3.6 s; exact comparison of every row of the five cases; helper equivalence tests; warm-up 11.9 s |
| H6 | Charts redrew whenever the evidence drawer opened | Stable evidence handler (`useCallback`) | UI test |

Full run: backend 242 passed (strict, none skipped), UI 66, browser 5. The five reference backlogs are unchanged: 74.4 / 212.2 / 93.1 / 164.4 / 77.3.

## I. Fourth round (2026-10-01): review of the previous two commits

| # | Finding | Fix | Verification |
|---|---|---|---|
| I1 | A blocked item's receipt was usable only if it arrived in a demand week | The recorded on-hand of a blocked item (and any buffer decision on it) is held apart and never usable; receipts are usable whenever they arrive | `test_review_fixes_4.py`; in the reference cases only item 44 changes (its leftover receipt now covers weeks 2-4); backlogs, work-centre results and lead times identical |
| I2 | Past-due receipts of any age were credited to week 1 | `past_due_receipt_max_days` (28, part of the data version); older lines are excluded and listed in `stale_receipts_excluded` | Unit tests; the one real past-due line is 3 days overdue and still counts |
| I3 | A week between routing revisions loaded zero hours | Capacity reports the item's operations as excluded; lead time returns unavailable (an item with no routing at all is unchanged) | Unit tests (no gaps in the synthetic data) |
| I4 | An FG with no BOM revision effective on the latest order date got a confident policy | INSUFFICIENT_EVIDENCE with a "no current BOM revision" blocker | Unit tests |
| I5 | The frozen lead-time reference still used the old missing-capacity rule | The reference mirrors the deliberate rule changes, listed in its docstring | Unit tests with a zero-capacity week and a routing gap |
| I6 | The "read-only" smoke test could call the external LLM | Copilot step opt-in with `PILOT_E2E_COPILOT=1` | Smoke run: 4 passed, 1 skipped |
| I7 | Concurrent BI exports shared temporary files | Unique temporary names per export | Unit test |
| I8 | The capacity card showed a bare "h/week" for missing hours | "n/a" | UI test |
| I9 | Routing data without `effective_to` crashed the engine | Missing end-date column means open-ended | Unit test |
| I10 | `security_setup` duplicated the connection URL builder | `Settings.odbc_url(user, password, database)` used by both | Unit test |

Decision (2026-10-01): a buffer decision on an entity-blocked item now counts as usable stock (see section K0 below); the recorded on-hand of a blocked item stays unusable.

Full run: backend 255 passed (strict, none skipped), UI 67, browser 4 passed and 1 skipped (copilot, opt-in). The five reference backlogs are unchanged: 74.4 / 212.2 / 93.1 / 164.4 / 77.3.

## J. Fifth round (2026-10-01)

| # | Finding | Fix | Verification |
|---|---|---|---|
| J1 | The loader preflight did not cover every file the load needs (e.g. `warehouses.csv`), so a missing file failed after the schema was dropped | `read_staged_tables` reads and parses every `TABLE_ORDER` file first and stops on any missing or unreadable one, even with `--skip-completeness`; the frames read are the ones loaded | `test_review_fixes_5.py` (missing and empty files, with and without `--skip-completeness`, nothing touched). Still possible after the drop: a type-conversion or constraint error; documented |
| J2 | Overlapping BOM revisions, or an expired BOM on a required subassembly, still gave confident policies | Every manufactured level of the current structure is checked; no effective revision or several overlapping ones is a blocker naming the item | Unit tests; live: exactly the three FGs on the injected overlapping revisions move from ASSEMBLE_TO_ORDER to INSUFFICIENT_EVIDENCE (ATO 17 to 14, insufficient 70 to 73) |
| J3 | A BI export interruption was detectable but the previous valid export was not guaranteed, contrary to INTEGRATION.md | The last verified pair is kept as `<name>.previous.*` before the live pair is replaced; `latest_valid(name)` returns a complete verified pair after an interruption at any point (except the very first export of a name) | Unit test crashing at each of the four renames |

Full run: backend 268 passed (strict, none skipped), UI 67, browser 4 passed and 1 skipped (copilot, opt-in). Engine results unchanged.

## K0. Blocked-item buffers (2026-10-01)

| # | Change | Verification |
|---|---|---|
| K0 | A planner's buffer decision is new stock, not a recorded balance, so it is usable even on an entity-blocked item; the item's recorded on-hand stays held apart | `test_review_fixes_4.py::test_blocked_on_hand_is_never_usable_but_a_buffer_decision_is`. Reference cases: BASELINE, DEMAND_SHOCK_ONLY and CAPACITY_ONLY identical; BUFFER_ONLY 93.1 to 70.0 and COMBINED 77.3 to 60.1 backlog hours (exact snapshot comparison). Week-12 CAB-100 lead time: buffer only 19.52 to 14.70 d, combined 15.39 to 12.29 d |

## K. Shop-floor flow simulator (2026-10-01)

Requests from the plant: a 40-minute colour change that could be avoided by delivering pieces in batches of one colour; a scrapped subcomponent only found at final inspection, scrapping the whole product and its good components; a shop order blocked by one missing component with no way to prioritise it; weekend overtime paid for work whose follow-on components were not delivered; and work in progress at each stage with the effect of a changed or cancelled forecast. Built as a separate discrete-event simulation (`backend/app/analytics/flow/`); the period engine and its five reference cases are untouched (exact snapshot comparison).

| # | Request | Implementation | Verification |
|---|---|---|---|
| K1 | Batch colours to avoid changeovers | Coating work centres charge 40 min for a colour change and a small reload otherwise; policy keeps the colour while a same-colour job is due within W days | `test_flow_sim.py` (changeover count and hours exact, grouping gives one change per extra colour, a zero window does not cross due dates). Live: 112 to 92 changes; no lateness change because the coating lines are lightly loaded here |
| K2 | Detect scrap earlier, replace only the bad component | Seeded per-unit defects; modes: final inspection scraps the product, final inspection replaces the failed component (rework and re-inspection), inspection of each component remakes only bad units | Forced-defect tests with hand-computed unit values (38 per finished good, 16 per component), reproducibility by seed. Live (mean of 10 seeded runs): value scrapped 339,018 / 38,555 / 12,611 EUR |
| K3 | Prioritise the one missing component | A component whose parent lacks at most one component, with the parent's other parts available and release passed, is served first | Two-product test where plain earliest-due-date serves the blocked order's part last; no effect on a plant without competition. Live: small gain in component wait, no change in late lots |
| K4 | Overtime only when it will be used | Saturday windows, always paid or gated on queued work whose consumer is missing nothing else by Monday; paid, busy, idle and left-waiting hours; an operation running at Friday close can use the new window | Tests for idle paid hours, refusal when nothing is waiting, productive overtime finishing earlier, refusal when the consumer lacks another part. Live: 182 of 240 paid hours idle when always paid |
| K5 | Work in progress at each stage and the effect of a forecast change | Stock points (after laser cutting, before painting) and a stage audit from the operation log; change impact splits the unwanted share of existing work into reusable (other open demand, same colour after painting) and stranded; lead time, late lots and hours per process before and after | Hand-computed values and waiting times, cancel / halve / raise, reuse by other demand, colour commitment, change before any work. A third stock point can be added by naming its process |
| K6 | Pages and route | Shop Floor Flow, Order Change Impact (14 pages), `POST /api/flow/change-impact` (operator role), every number with evidence and the assumptions | `test_flow_pages.py`, `test_cp4_cp5.py` (every page has evidence), 6 UI tests, browser smoke over all 14 pages |

| K7 | Interface completion and weekly forecasts | Settings panel on Shop Floor Flow (`POST /api/flow/run`, bounded, operator role); Stock Points page (three points incl. between welding and painting, weekly history, day audit); Forecast Updates page replaying the data's weekly snapshots; idle hours on the Monday after paid overtime | `test_flow_pages.py`, `test_flow_analysis.py`, `test_flow_sim.py`, 5 more UI tests, browser smoke over all 16 pages. Weekly average divides by 7 days (a unit test caught a partial-week overstatement); sparse snapshots: a gap is not a zero forecast |

Calibration: simulated processing hours are about 0.9 of the period engine's required hours for the same demand and weeks (regression test 0.6-1.15). Not claimed: that any colour mix, defect rate, rework time or overtime premium is the plant's (all assumptions, listed in each drawer); that these policies would behave the same in another plant.

Full run: backend 337 passed (strict, none skipped), UI 78, browser 4 passed and 1 skipped (copilot, opt-in). The five reference backlogs are 74.4 / 212.2 / 70.0 / 164.4 / 60.1 since K0.

