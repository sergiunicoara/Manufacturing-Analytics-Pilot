# Data model

The authoritative types and foreign keys are in `db/ddl/001_schema.sql`. This diagram highlights analytical lineage.

```mermaid
erDiagram
    ITEMS ||--o{ BOM_HEADERS : parent
    BOM_HEADERS ||--o{ BOM_COMPONENTS : contains
    ITEMS ||--o{ ROUTING_HEADERS : routed_by
    ROUTING_HEADERS ||--o{ ROUTING_OPERATIONS : has
    WORK_CENTRES ||--o{ ROUTING_OPERATIONS : performs
    WORK_CENTRES ||--o{ CAPACITY_CALENDAR : scheduled
    FORECAST_VERSIONS ||--o{ CUSTOMER_FORECASTS : snapshots
    SALES_ORDERS ||--o{ SALES_ORDER_LINES : contains
    SCENARIO_RUNS ||--o{ MATERIAL_REQUIREMENTS : yields
    SCENARIO_RUNS ||--o{ PERIOD_ENGINE_RESULTS : yields
    SCENARIO_RUNS ||--o{ BUFFER_RECOMMENDATIONS : yields
    ITEMS ||--o{ MATERIAL_REQUIREMENTS : item
    WORK_CENTRES ||--o{ PERIOD_ENGINE_RESULTS : centre
```

Effective dates on BOM and routing versions are part of calculation inputs. `scenario_runs.parameters_json` and `seed` identify the calculation assumptions. `material_requirements` and `period_engine_results` preserve each week's result. Data quality findings are maintained separately so invalid records remain auditable. Source tables are synthetic analogues of ERP/MES data and do not imply a live integration.
