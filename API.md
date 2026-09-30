# API

The local API is documented interactively at `/docs`.

| Route | Purpose |
| --- | --- |
| `GET /health` | Process health. |
| `GET /api/pages/{slug}` | Evidence-rich read model for each of the twelve pages. |
| `GET /kpi/plant-overview` | Plant Overview alias. |
| `GET /api/executive-story` | Five-case demand-weighted lead-time series and calendar translation. |
| `GET /story` | Live-number scripted executive beats. |
| `POST /api/scenarios/run` | Run and persist a bounded custom scenario. |
| `POST /copilot/ask` | Deterministic evidence lookup and optional prose explanation. |
| `GET /api/stage-performance/records` | Operations by record class / work centre, paginated (`offset`, `limit` ≤ 1000). |
| `GET /api/dq/findings`, `GET /api/dq/findings.csv` | Full DQ findings, filterable and paginated; complete CSV export. |
| `GET /api/parameters/package.json` / `.csv` / `schema.json` | Versioned planner review package and its JSON Schema (no ERP write-back). |

Page slugs: `plant-overview`, `demand-forecast`, `production-flow`, `bom-explorer`, `capacity`, `wip-lead-time`, `scenario-lab`, `data-quality`, `recommendation`, `stage-performance`, `decision-economics`, `planning-policy`. `bom-explorer` accepts `item_id`.

Every metric and chart point has an `evidence` object with value, provenance, formula reference, named inputs with source IDs, calculation trace, and assumptions. Scenario requests accept demand multiplier, buffer units per target item, capacity multiplier, and intervention start week. The request bounds are enforced by Pydantic. A successful run returns its persisted `run_id`.

The copilot request accepts `question`, optional deterministic `tool`, and optional `scenario`. Tool names are `get_kpi`, `explain_recommendation`, `get_dq_findings`, `get_cost`, and `run_scenario`. A question may invoke several deterministic tools in sequence; any missing result stops the response with `insufficient_evidence: true` and `llm_used: false`. If an Anthropic key is configured, `ANTHROPIC_MODEL` selects the prose model; the raw tool bundle is always returned.

**Authentication.** In the `demo` profile no key is needed. In the `secured` profile every route except `/health` requires `X-API-Key`: a reader key for reads, an operator key for `POST /api/scenarios/run` and `POST /copilot/ask`. See [docs/SECURITY.md](docs/SECURITY.md).
