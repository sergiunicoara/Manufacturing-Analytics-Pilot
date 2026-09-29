# Implementation prompt: finish the manufacturing analytics pilot and its demonstration

Use this prompt in a coding-agent session at this repository's root.

---

Implement all remaining requirements of this Manufacturing Analytics Pilot, then finish its demonstration movie. Complete working code, database changes, APIs, UI, tests, operational tooling and documentation. Do not stop after an audit, a plan, documentation-only substitutes for executable features, or another declaration that CP1–CP5 are complete.

## 1. Establish the actual baseline

Read applicable AGENTS.md instructions, PLAN.md, tasks/todo.md, tasks/lessons.md, ANALYTICS_METHODS.md, PILOT_FINDINGS.md, DATA_MODEL.md, DATA_QUALITY.md, ASSUMPTIONS.md, LIMITATIONS.md, ARCHITECTURE.md, OPERATIONS.md, API.md and the film sources under docs/demo_film/. Inspect the implementation behind the claims.

The current system has a synthetic SQL Server dataset, Python period engine, scoped DQ handling, forecasts, BOM netting, capacity/constraint analysis, buffer candidates, scenario persistence, nine dashboard pages, Evidence Drawer, Executive Story and an evidence-gated copilot. Preserve these capabilities and the user's uncommitted work. Inspect Git status first; work in the existing checkout unless isolation is necessary. Do not commit, push, deploy, contact external parties or change real ERP data without explicit authorization.

Historical checkpoint labels and test counts are not completion evidence. Create docs/REQUIREMENTS_COMPLETION_MATRIX.md mapping every substantive PLAN.md requirement and each requirement below to implementation, verification command/output, status and limitations. Distinguish original plan omissions, approved analytical corrections, and additional ERP-pilot requirements in this prompt. Where an original job description is unavailable, say so; do not invent its exact wording.

Reconcile superseded plan text: the later BOM-aware, period-entry lead-time correction takes precedence over the earlier piecewise queue sketch. Preserve intentional deviations with a rationale. Do not implement obsolete formulas merely to match old prose. Add any other genuine unimplemented requirement discovered by this complete plan-to-code review to the worklist.

## 2. Preserve the analytical invariants

- All five reference cases use the same period-stepped physical engine: BASELINE, DEMAND_SHOCK_ONLY, BUFFER_ONLY, CAPACITY_ONLY and COMBINED. Keep custom saved runs distinct.
- Preserve demand, material and work-hour conservation. For a period: starting backlog + required hours = completed hours + ending backlog.
- Capacity never reduces intrinsic processing standards. Buffers alter inventory/net production need, not gross demand or effective capacity.
- Projected lead time follows the finished-good route plus the longest manufactured BOM branch. Retain processing, standard/congestion queue, transfer and total components. Congestion uses backlog ahead at entry divided by effective capacity per workday, consistently converted to calendar days. Preserve temporal lag and unchanged pre-intervention periods; never smooth or rewrite historical points to obtain a desired result.
- Keep the 12-week presentation horizon and visible baseline deterioration. Support longer engine horizons. Do not call the baseline a stable control or enforce a preferred ranking among interventions.
- Do not hardcode the previous scenario values, recovery multiplier or test count into logic. Recompute them from the running system and report changes with reasons.
- Every added result carries formula, units, time scope, source records, assumptions, exclusions and coverage. Retain the existing evidence contract. Synthetic origin is separate from MEASURED/DERIVED/ASSUMED calculation provenance; generated timestamps are not real plant observations.

## 3. Implement historical stage-performance analytics

Use production_order_operations actual timestamps and production/routing/work-centre relationships to calculate recorded operation elapsed time. Expose counts, coverage, median and upper percentiles by appropriate item, stage/work centre, site and period, with inspectable underlying records.

Define completed-operation cohorts and observation windows. Handle missing dates, open operations, invalid ordering, duplicate records, overlapping operations, timezone assumptions and DQ exclusions explicitly. Report unfinished operations separately; do not silently treat them as completed, zero-duration observations.

An actual finish-minus-start interval is elapsed operation time, not automatically productive processing time. Consecutive operation gaps are inter-operation elapsed gaps, not proven queue time. Distinguish these from modeled processing/queue/transfer unless the source event semantics support the decomposition. Do not clamp negative/overlapping gaps into plausible queue estimates.

Show historical recorded metrics beside projected metrics in the UI, with distinct labels and compatible aggregation scopes. Avoid claiming forecast accuracy from unrelated historical cohorts. Add fixtures and tests for missing/open/invalid timestamps, aggregation, exclusions, coverage and provenance. Clearly state the current timestamps are synthetic.

## 4. Implement the missing cost and decision-economics layer

Add a deterministic cost module using effective-dated standard_costs, valid routing/BOM inputs, work-centre hourly rates and period-engine outputs. Inspect existing cost-record semantics first: generated standard costs already contain material, labour and overhead components, and some deeper material values are approximations. Do not double-count rolled-up material, labour or overhead or relabel estimates as observed expenditure.

Define currency, units, valuation dates, overhead basis and cost coverage. The plan's shorthand cannot add a percentage directly to currency: apply each percentage to an explicitly documented monetary base. Respect setup batches, yield/scrap and effective revisions where relevant. Missing or overlapping costs must produce unknown/blocked or explicitly assumed results, never silent zeroes.

Implement separately:

- Product/unit cost with a transparent component breakdown.
- Inventory/buffer capital exposure and changes from the comparison case.
- Period-based WIP carrying cost using a documented valuation and exposure-time convention.
- Incremental intervention cost from additional scheduled/paid hours and explicit labour/overtime/equipment assumptions. Effective productive hours are not automatically paid hours.
- Five-case summaries and deltas showing residual backlog/service risk alongside cost.

If the engine's aggregate WIP cannot support item-level valuation without unsupported allocation, add an auditable allocation or report the affected valuation as unavailable with coverage. Do not multiply mixed-item WIP by an arbitrary average cost. Separate capital tied up, purchase cash flow, period expense and assumed avoided-loss estimates. Do not present an ROI or profit improvement when the required inputs are absent.

Persist cost assumptions/results with run identity and expose them through APIs, dashboard evidence and deterministic copilot tools. Test units, arithmetic, effective dating, missing data, non-double-counting, exposure timing and scenario deltas. Reuse the physical engine; do not redesign it for financial presentation.

## 5. Implement planning-policy recommendations and a reviewable parameter export

Create an explainable heuristic for make-to-stock, make-to-order and assemble-to-order where applicable, plus an insufficient-evidence outcome. Use available demand variability, intermittency, forecast error, replenishment/lead time, commonality and service assumptions. Missing inputs must affect confidence and eligibility rather than silently becoming defaults.

Identify candidate decoupling locations from the BOM/routing and constraints, distinguish policy selection from buffer sizing, and expose reasons, thresholds, assumptions and alternatives. Keep thresholds configurable and test boundary cases. Use the existing term Analytical Buffer Recommendation; do not claim DDMRP certification or introduce normative buffer-zone terminology.

Export a versioned CSV/JSON parameter package for planner review: business keys/site, policy recommendation, proposed parameter values and units, effective date, source run, provenance, confidence, blockers and mapping status. Mark unsupported native M3 fields unmapped. Provide schema validation and round-trip tests. This is a review/export workflow with no ERP write-back; do not imply that generic recommendations are an import-ready M3 transaction format.

## 6. Build the ERP data request, mapping and integration boundaries

Create DATA_REQUEST.md with required/optional datasets, keys, units, timestamps, customer/item/site scopes, revision history, requested coverage, owners and questions for the ERP director. Implement an executable completeness/profile checker producing pass/warn/block results against a configurable request contract. Row counts alone do not establish completeness: check relationships, dates, history length, semantics and field presence without inventing missing data.

Create an explicit M3-to-canonical mapping with business keys, joins, units, date conversions and version/status fields. Treat familiar M3 table names as hypotheses until verified against authoritative vendor material or an actual source schema. Cite verified sources and label client-specific mappings pending. Do not invent an M3 field or claim version compatibility from generic table names.

Implement the planned ERPAdapter, BIExportAdapter and MESAdapter protocols with executable simulated file adapters and contract tests. Preserve the canonical model. Include sample import/export flows and validation errors. No real external integration is implied.

Create INTEGRATION.md with a Mermaid diagram of M3/IBM i → agreed SQL Server extract/backup → validation/canonical layer → analytics → Qlik Cloud/Bright Analytics exports, and the MES boundary. Define source ownership, read-only permissions, refresh cadence, late data, identifiers, reconciliation, failure/retry behavior and proposed incremental extraction. Explain what runs locally versus what requires the client's systems and agreement.

## 7. Demonstrate T-SQL and a safe backup-restore workflow

Add versioned, rerunnable T-SQL read models for forecast revisions/accuracy, persisted weekly load and recorded stage elapsed time, with stable grains, documented joins, units and evidence lineage. Define one authoritative formula per metric and prove SQL/Python parity where both implement the same metric. Do not create conflicting duplicate calculations. Expose views suitable for a read-only BI user and document representative queries.

Provide scripts and a runbook for backup inspection, RESTORE VERIFYONLY, RESTORE FILELISTONLY and RESTORE DATABASE WITH MOVE. Parameterize paths and database names safely. Refuse overwriting existing databases by default; do not use WITH REPLACE or delete existing data during demonstration. Validate dynamic identifiers and path inputs. Demonstrate a backup/restore round trip using a newly named disposable database in the authorized local stack, then verify schema/data checks. VERIFYONLY by itself is not proof of a successful restore or data completeness.

## 8. Implement a practical security boundary

Separate the local synthetic demo profile from any secured deployment profile. Remove silent default-password reliance in the secured profile, keep secrets out of Git/logs, bind local development ports appropriately, and add tested API authentication/authorization for the secured profile, including scenario mutations. Keep browser credentials out of generated bundles and documentation examples.

Use separate read-only extraction/BI accounts and a narrowly scoped application identity; the application needs appropriate scenario-persistence permissions and must not use a reader account or SA indiscriminately. Demonstrate least privilege against the local test database.

Document EU hosting/residency requirements, encryption in transit/at rest and backups, retention, pseudonymisation, audit access and incident responsibilities in SECURITY.md. Distinguish implemented local controls, tested controls and infrastructure/client approvals still required. A Docker configuration or written runbook is not evidence of EU deployment or legal compliance.

External LLM use with sensitive/client data must require explicit configuration and authorization. Default to deterministic evidence-only operation for that profile. Minimize/redact payloads, document what leaves the process and prevent keys/evidence from leaking into logs. Do not assume that setting a region string guarantees EU processing: verify the selected provider/deployment capability from current authoritative documentation, or keep external explanations disabled. Preserve the existing insufficient-evidence gate and test that no provider call occurs without matching evidence and allowed configuration.

## 9. Resolve reporting gaps and analytical ambiguities

- Correct stale 2.5× wording in LIMITATIONS.md and PLAN.md: distinguish the historical long-horizon calibration from the recomputed 12-week result. Show current/target scheduled and effective hours separately and validate calendar/downtime arithmetic; keep operational feasibility unproven until supported.
- Explain DQ counts by rule, origin, classification, impact scope, unique affected entities and analytical coverage. The share of findings marked blocking is not the share of unusable data. Provide pagination/filtering or full export rather than silently truncating the reviewable findings to 200 rows.
- Document forecast consumption as currently implemented: nearest eligible bucket within ±4 weeks, without automatic spillover. Make that policy explicit in evidence and test it, including no double-counting, customer/item/site isolation and delivery-period handling. Do not change the consumption policy merely because a different policy is common; introduce alternatives explicitly if required and compare their consequences.
- Verify persistence, data-version identity and cache invalidation when source data, assumptions or analytical code change. A seed and parameter match alone must not serve stale results from different inputs. Preserve audit history.
- Inspect any allegedly orphaned report process before intervening. A past CPU-time observation is not authorization to kill a current process or proof that it is abandoned.

## 10. Integrate, verify and update the demonstration

Wire new capabilities into the existing UI/API and shared Evidence Drawer with loading, missing-evidence and error states. Demonstrate a coherent path from source completeness and recorded performance through forecasts, physical constraints, intervention economics, policy recommendations and the parameter export. Extend existing navigation as needed without a gratuitous dashboard redesign.

Run meaningful targeted tests while implementing, then the full backend suite and frontend build at the final gate. Exercise SQL migrations/read models, isolated restore and security modes against the running local stack. Verify the actual browser workflows; mocked responses and compilation are insufficient. Record commands, timestamps, results and limitations; never reuse a historical pass count as fresh verification. Run all five scenarios and retain their weekly outputs, component lead times, cost results and reconciliation evidence. Investigate changed numerical results rather than retuning them to previous values.

Update the completion matrix, task status and all affected docs. Mark each item implemented and verified, implemented but not verifiable here, or externally blocked with an exact reason. Real client connectivity/hosting approvals may remain external; complete and test the local implementation without pretending those approvals exist. Do not label the entire project complete while mandatory executable requirements remain absent.

Finally update docs/demo_film/SCENES.json and regenerate SCRIPT.md, BUILD_MOVIE_PROMPT.md as needed, capture checklist and deck companions. Add the new capabilities and use current results. Record the running application and real verification output, create the narration/captions, render the 1080p and 720p movies, and review the final cut. Follow the existing evidence and capture gates. Never substitute editorial cards for absent product footage, fabricate terminal output, hardcode plausible lead-time curves or automatically mark unreviewed captures approved. Disclose synthetic data and AI narration; use only authorized voice services and licensed supplied music. Where external voice access is unavailable, still complete real footage and a captioned draft and report the specific narration blocker.

## Execution and completion contract

Work in reviewable stages: (1) requirement reconciliation, (2) historical metrics/DQ, (3) cost/policy/export, (4) integration/SQL/restore/security, (5) end-to-end verification/docs, (6) film. Resolve routine implementation choices autonomously and document them. Ask only for genuinely missing external facts or authorization; continue independent work when an external dependency is blocked.

Do not weaken tests, hide missing inputs, redesign validated physical behavior without a demonstrated correctness issue, or turn unknowns into fabricated data. If a correctness issue is found, explain it, add a regression case, fix it narrowly and rerun affected surfaces.

Deliver working changes, the evidence-backed completion matrix, representative exports and reports, updated operational docs and the reviewed movie artifacts or precise external blockers. State what is verified and what remains unresolved. Stop only when the authorized local work is complete or a concrete blocker prevents further progress.
