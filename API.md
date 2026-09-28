# API

The local API is documented interactively at `/docs`.

| Route | Purpose |
| --- | --- |
| `GET /health` | Process health. |
| `GET /api/pages/{slug}` | Evidence-rich read model for each of the nine pages. |
| `GET /kpi/plant-overview` | Plant Overview alias. |
| `GET /api/executive-story` | Five-case demand-weighted lead-time series and calendar translation. |
| `GET /story` | Live-number scripted executive beats. |
| `POST /api/scenarios/run` | Run and persist a bounded custom scenario. |
| `POST /copilot/ask` | Deterministic evidence lookup and optional prose explanation. |

Page slugs: `plant-overview`, `demand-forecast`, `production-flow`, `bom-explorer`, `capacity`, `wip-lead-time`, `scenario-lab`, `data-quality`, `recommendation`. `bom-explorer` accepts `item_id`.

Every metric and chart point has an `evidence` object with value, provenance, formula reference, named inputs with source IDs, calculation trace, and assumptions. Scenario requests accept demand multiplier, buffer units per target item, capacity multiplier, and intervention start week. The request bounds are enforced by Pydantic. A successful run returns its persisted `run_id`.

The copilot request accepts `question`, optional deterministic `tool`, and optional `scenario`. Tool names are `get_kpi`, `explain_recommendation`, `get_dq_findings`, and `run_scenario`. A question may invoke several deterministic tools in sequence; any missing result stops the response with `insufficient_evidence: true` and `llm_used: false`. If an Anthropic key is configured, `ANTHROPIC_MODEL` selects the prose model; the raw tool bundle is always returned.
