-- Manufacturing Analytics Pilot — synthetic ERP + analytics schema
-- SQL Server 2022. This file is the single source of truth for the schema;
-- the Python layer uses SQLAlchemy reflection against it rather than a
-- parallel set of declarative models, to avoid schema drift.
--
-- IMPORTANT: this database holds entirely SYNTHETIC data describing a
-- fictional plant. It is not connected to, and does not represent, any real
-- Infor M3 / IBM i / Qlik Cloud / Bright Analytics / MES system.
--
-- Deliberate relational-integrity gaps: a handful of FK-shaped columns are
-- NOT enforced with a real FOREIGN KEY constraint, specifically so the
-- Data Quality Engine (Phase 3) has real orphan/dangling records to detect,
-- the way a real ERP extract often does. Each is called out with a comment
-- "-- DQ: intentionally unenforced". A few business-key columns are also
-- deliberately left without a UNIQUE constraint for the same reason
-- (duplicate item codes / sales order numbers). This is documented again in
-- DATA_QUALITY.md.

IF DB_ID('mfg_analytics_pilot') IS NULL
BEGIN
    CREATE DATABASE mfg_analytics_pilot;
END
GO

USE mfg_analytics_pilot;
GO

-- ============================================================
-- Drop in dependency order (dev convenience — safe to re-run)
-- ============================================================
IF OBJECT_ID('buffer_recommendations', 'U') IS NOT NULL DROP TABLE buffer_recommendations;
IF OBJECT_ID('period_engine_results', 'U') IS NOT NULL DROP TABLE period_engine_results;
IF OBJECT_ID('material_requirements', 'U') IS NOT NULL DROP TABLE material_requirements;
IF OBJECT_ID('scenario_runs', 'U') IS NOT NULL DROP TABLE scenario_runs;
IF OBJECT_ID('dq_findings', 'U') IS NOT NULL DROP TABLE dq_findings;
IF OBJECT_ID('wip', 'U') IS NOT NULL DROP TABLE wip;
IF OBJECT_ID('inventory', 'U') IS NOT NULL DROP TABLE inventory;
IF OBJECT_ID('standard_costs', 'U') IS NOT NULL DROP TABLE standard_costs;
IF OBJECT_ID('capacity_calendar', 'U') IS NOT NULL DROP TABLE capacity_calendar;
IF OBJECT_ID('purchase_order_lines', 'U') IS NOT NULL DROP TABLE purchase_order_lines;
IF OBJECT_ID('purchase_orders', 'U') IS NOT NULL DROP TABLE purchase_orders;
IF OBJECT_ID('production_order_operations', 'U') IS NOT NULL DROP TABLE production_order_operations;
IF OBJECT_ID('production_orders', 'U') IS NOT NULL DROP TABLE production_orders;
IF OBJECT_ID('forecast_consumption', 'U') IS NOT NULL DROP TABLE forecast_consumption;
IF OBJECT_ID('sales_order_lines', 'U') IS NOT NULL DROP TABLE sales_order_lines;
IF OBJECT_ID('sales_orders', 'U') IS NOT NULL DROP TABLE sales_orders;
IF OBJECT_ID('customer_forecasts', 'U') IS NOT NULL DROP TABLE customer_forecasts;
IF OBJECT_ID('forecast_versions', 'U') IS NOT NULL DROP TABLE forecast_versions;
IF OBJECT_ID('routing_operations', 'U') IS NOT NULL DROP TABLE routing_operations;
IF OBJECT_ID('routing_headers', 'U') IS NOT NULL DROP TABLE routing_headers;
IF OBJECT_ID('bom_components', 'U') IS NOT NULL DROP TABLE bom_components;
IF OBJECT_ID('bom_headers', 'U') IS NOT NULL DROP TABLE bom_headers;
IF OBJECT_ID('work_centres', 'U') IS NOT NULL DROP TABLE work_centres;
IF OBJECT_ID('items', 'U') IS NOT NULL DROP TABLE items;
IF OBJECT_ID('suppliers', 'U') IS NOT NULL DROP TABLE suppliers;
IF OBJECT_ID('customers', 'U') IS NOT NULL DROP TABLE customers;
IF OBJECT_ID('warehouses', 'U') IS NOT NULL DROP TABLE warehouses;
IF OBJECT_ID('sites', 'U') IS NOT NULL DROP TABLE sites;
GO

-- ============================================================
-- Core master data
-- ============================================================

CREATE TABLE sites (
    site_id         INT IDENTITY(1,1) PRIMARY KEY,
    site_code       VARCHAR(20) NOT NULL UNIQUE,
    name            NVARCHAR(200) NOT NULL,
    country         VARCHAR(2) NOT NULL,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE warehouses (
    warehouse_id    INT IDENTITY(1,1) PRIMARY KEY,
    warehouse_code  VARCHAR(20) NOT NULL UNIQUE,
    site_id         INT NOT NULL REFERENCES sites(site_id),
    name            NVARCHAR(200) NOT NULL,
    warehouse_type  VARCHAR(20) NOT NULL, -- RAW / WIP / FG
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE customers (
    customer_id     INT IDENTITY(1,1) PRIMARY KEY,
    customer_code   VARCHAR(20) NOT NULL UNIQUE,
    name            NVARCHAR(200) NOT NULL,
    country         VARCHAR(2) NOT NULL,
    region          VARCHAR(50) NULL,
    credit_status   VARCHAR(20) NOT NULL DEFAULT 'OK', -- OK / HOLD / WATCH
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE suppliers (
    supplier_id             INT IDENTITY(1,1) PRIMARY KEY,
    supplier_code           VARCHAR(20) NOT NULL UNIQUE,
    name                    NVARCHAR(200) NOT NULL,
    country                 VARCHAR(2) NOT NULL,
    default_lead_time_days  INT NOT NULL,
    created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE items (
    item_id         INT IDENTITY(1,1) PRIMARY KEY,
    -- DQ: intentionally NOT unique — a handful of duplicate item_codes are
    -- injected by the generator to exercise the "duplicate item codes" rule.
    item_code       VARCHAR(40) NOT NULL,
    description     NVARCHAR(300) NOT NULL,
    item_type       VARCHAR(20) NOT NULL, -- FG / SUBASSY / RAW / PACKAGING
    uom             VARCHAR(10) NOT NULL, -- deliberately includes a few invalid values
    product_family  VARCHAR(30) NULL,     -- CAB-100 / CAB-200 / ENC-300 / RACK-400 / BOX-500
    is_active       BIT NOT NULL DEFAULT 1,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_items_item_code ON items(item_code);
CREATE INDEX ix_items_item_type ON items(item_type);

CREATE TABLE work_centres (
    work_centre_id      INT IDENTITY(1,1) PRIMARY KEY,
    work_centre_code    VARCHAR(20) NOT NULL UNIQUE,
    name                NVARCHAR(100) NOT NULL,
    site_id             INT NOT NULL REFERENCES sites(site_id),
    process_type        VARCHAR(40) NOT NULL, -- LASER / BEND / WELD / PAINT / ASSEMBLY / ...
    shifts_per_day      INT NOT NULL,
    hours_per_shift     DECIMAL(5,2) NOT NULL,
    days_per_week       INT NOT NULL,
    cost_per_hour       DECIMAL(10,2) NOT NULL,
    created_at          DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

-- ============================================================
-- BOM / Routing
-- ============================================================

CREATE TABLE bom_headers (
    bom_id          INT IDENTITY(1,1) PRIMARY KEY,
    parent_item_id  INT NOT NULL REFERENCES items(item_id),
    revision        VARCHAR(10) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE / EXPIRED / DRAFT
    effective_from  DATE NOT NULL,
    effective_to    DATE NULL,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_bom_headers_parent ON bom_headers(parent_item_id);

CREATE TABLE bom_components (
    bom_component_id    INT IDENTITY(1,1) PRIMARY KEY,
    bom_id              INT NOT NULL REFERENCES bom_headers(bom_id),
    -- DQ: intentionally NOT a real FK — a handful of orphan component
    -- references are injected (component_item_id pointing at a non-existent
    -- or retired item) to exercise the "orphan BOM component" rule.
    component_item_id   INT NOT NULL,
    quantity_per        DECIMAL(12,4) NOT NULL,
    scrap_pct           DECIMAL(6,4) NOT NULL DEFAULT 0,
    effective_from      DATE NOT NULL,
    effective_to        DATE NULL,
    created_at          DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_bom_components_bom ON bom_components(bom_id);
CREATE INDEX ix_bom_components_component ON bom_components(component_item_id);

CREATE TABLE routing_headers (
    routing_id      INT IDENTITY(1,1) PRIMARY KEY,
    item_id         INT NOT NULL REFERENCES items(item_id),
    revision        VARCHAR(10) NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE NULL,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_routing_headers_item ON routing_headers(item_id);

CREATE TABLE routing_operations (
    routing_operation_id        INT IDENTITY(1,1) PRIMARY KEY,
    routing_id                  INT NOT NULL REFERENCES routing_headers(routing_id),
    seq_no                      INT NOT NULL,
    -- DQ: intentionally NOT a real FK, and nullable — a handful of operations
    -- are missing their work-centre mapping.
    work_centre_id              INT NULL,
    operation_name              VARCHAR(60) NOT NULL,
    setup_time_minutes          DECIMAL(10,2) NULL,   -- DQ: some deliberately missing
    run_time_minutes_per_unit   DECIMAL(10,4) NULL,   -- DQ: some deliberately missing/implausible
    queue_time_minutes          DECIMAL(10,2) NOT NULL DEFAULT 0,
    transfer_time_minutes       DECIMAL(10,2) NOT NULL DEFAULT 0,
    yield_pct                   DECIMAL(6,4) NOT NULL DEFAULT 1,
    batch_size                  INT NOT NULL DEFAULT 1,
    created_at                  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at                  DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_routing_operations_routing ON routing_operations(routing_id);
CREATE INDEX ix_routing_operations_wc ON routing_operations(work_centre_id);

-- ============================================================
-- Demand — scoped by customer + item + site + delivery bucket [CORR-3]
-- ============================================================

CREATE TABLE forecast_versions (
    forecast_version_id INT IDENTITY(1,1) PRIMARY KEY,
    snapshot_date        DATE NOT NULL,
    description          NVARCHAR(200) NULL,
    created_at           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_forecast_versions_snapshot ON forecast_versions(snapshot_date);

CREATE TABLE customer_forecasts (
    forecast_id             INT IDENTITY(1,1) PRIMARY KEY,
    forecast_version_id     INT NOT NULL REFERENCES forecast_versions(forecast_version_id),
    -- DQ: intentionally NOT a real FK — a handful of forecasts reference a
    -- customer_id that does not exist, to exercise "forecast records without
    -- matching customers".
    customer_id             INT NOT NULL,
    item_id                 INT NOT NULL REFERENCES items(item_id),
    site_id                 INT NOT NULL REFERENCES sites(site_id),
    delivery_period_start   DATE NOT NULL, -- weekly bucket start
    qty                     DECIMAL(14,2) NOT NULL,
    created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_customer_forecasts_lookup ON customer_forecasts(customer_id, item_id, site_id, delivery_period_start);
CREATE INDEX ix_customer_forecasts_version ON customer_forecasts(forecast_version_id);

CREATE TABLE sales_orders (
    sales_order_id  INT IDENTITY(1,1) PRIMARY KEY,
    -- DQ: intentionally NOT unique — a couple of duplicate order numbers are
    -- injected to exercise "duplicate sales orders".
    order_number    VARCHAR(30) NOT NULL,
    customer_id     INT NOT NULL REFERENCES customers(customer_id),
    site_id         INT NOT NULL REFERENCES sites(site_id),
    order_date      DATE NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_sales_orders_customer ON sales_orders(customer_id);

CREATE TABLE sales_order_lines (
    line_id                 INT IDENTITY(1,1) PRIMARY KEY,
    sales_order_id          INT NOT NULL REFERENCES sales_orders(sales_order_id),
    item_id                 INT NOT NULL REFERENCES items(item_id),
    qty                     DECIMAL(14,2) NOT NULL,
    unit_price              DECIMAL(12,2) NOT NULL,
    requested_ship_date     DATE NOT NULL,
    promised_ship_date      DATE NULL,
    created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_sales_order_lines_order ON sales_order_lines(sales_order_id);
CREATE INDEX ix_sales_order_lines_item ON sales_order_lines(item_id);

CREATE TABLE forecast_consumption (
    consumption_id      INT IDENTITY(1,1) PRIMARY KEY,
    forecast_id          INT NOT NULL REFERENCES customer_forecasts(forecast_id),
    sales_order_line_id  INT NOT NULL REFERENCES sales_order_lines(line_id),
    consumed_qty         DECIMAL(14,2) NOT NULL,
    consumption_date     DATE NOT NULL,
    created_at           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_forecast_consumption_forecast ON forecast_consumption(forecast_id);

-- ============================================================
-- Execution
-- ============================================================

CREATE TABLE production_orders (
    production_order_id    INT IDENTITY(1,1) PRIMARY KEY,
    order_number            VARCHAR(30) NOT NULL UNIQUE,
    item_id                 INT NOT NULL REFERENCES items(item_id),
    bom_id                  INT NULL REFERENCES bom_headers(bom_id),
    -- DQ: intentionally NOT a real FK, and nullable — a handful of production
    -- orders reference a missing/retired routing, or have no routing at all,
    -- to exercise "production orders without valid routing".
    routing_id              INT NULL,
    site_id                 INT NOT NULL REFERENCES sites(site_id),
    qty                     DECIMAL(14,2) NOT NULL,
    status                  VARCHAR(20) NOT NULL, -- PLANNED / RELEASED / IN_PROGRESS / COMPLETED
    planned_start           DATE NOT NULL,
    planned_finish          DATE NOT NULL,
    actual_start            DATE NULL,
    actual_finish           DATE NULL,
    created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_production_orders_item ON production_orders(item_id);
CREATE INDEX ix_production_orders_status ON production_orders(status);

CREATE TABLE production_order_operations (
    po_operation_id         INT IDENTITY(1,1) PRIMARY KEY,
    production_order_id     INT NOT NULL REFERENCES production_orders(production_order_id),
    routing_operation_id    INT NULL REFERENCES routing_operations(routing_operation_id),
    seq_no                  INT NOT NULL,
    work_centre_id          INT NULL REFERENCES work_centres(work_centre_id),
    planned_start            DATETIME2 NULL,
    planned_finish           DATETIME2 NULL,
    actual_start             DATETIME2 NULL,
    actual_finish            DATETIME2 NULL,
    status                   VARCHAR(20) NOT NULL,
    created_at               DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_po_operations_po ON production_order_operations(production_order_id);
CREATE INDEX ix_po_operations_wc ON production_order_operations(work_centre_id);

CREATE TABLE purchase_orders (
    purchase_order_id   INT IDENTITY(1,1) PRIMARY KEY,
    order_number          VARCHAR(30) NOT NULL UNIQUE,
    supplier_id           INT NOT NULL REFERENCES suppliers(supplier_id),
    site_id               INT NOT NULL REFERENCES sites(site_id),
    order_date            DATE NOT NULL,
    status                VARCHAR(20) NOT NULL, -- OPEN / CLOSED / CANCELLED
    created_at            DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at            DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE purchase_order_lines (
    po_line_id              INT IDENTITY(1,1) PRIMARY KEY,
    purchase_order_id       INT NOT NULL REFERENCES purchase_orders(purchase_order_id),
    item_id                 INT NOT NULL REFERENCES items(item_id),
    qty_ordered             DECIMAL(14,2) NOT NULL,
    qty_received            DECIMAL(14,2) NOT NULL DEFAULT 0,
    unit_cost               DECIMAL(12,4) NOT NULL,
    expected_receipt_date   DATE NOT NULL,
    actual_receipt_date     DATE NULL,
    status                  VARCHAR(20) NOT NULL, -- OPEN / RECEIVED / PARTIAL / CANCELLED
    created_at              DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_po_lines_item ON purchase_order_lines(item_id);
CREATE INDEX ix_po_lines_order ON purchase_order_lines(purchase_order_id);

-- ============================================================
-- Capacity — three explicit tiers [CORR-5]
-- ============================================================

CREATE TABLE capacity_calendar (
    capacity_calendar_id    INT IDENTITY(1,1) PRIMARY KEY,
    work_centre_id           INT NOT NULL REFERENCES work_centres(work_centre_id),
    week_start_date          DATE NOT NULL,
    calendar_hours           DECIMAL(10,2) NOT NULL,   -- shifts x hours/shift x days/week
    planned_downtime_hours   DECIMAL(10,2) NOT NULL DEFAULT 0,
    availability_pct         DECIMAL(6,4) NOT NULL,    -- unplanned-loss factor
    available_hours          DECIMAL(10,2) NOT NULL,   -- calendar - planned_downtime (stored)
    effective_hours          DECIMAL(10,2) NOT NULL,   -- available x availability_pct (stored)
    created_at               DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE UNIQUE INDEX ux_capacity_calendar_wc_week ON capacity_calendar(work_centre_id, week_start_date);

CREATE TABLE standard_costs (
    standard_cost_id    INT IDENTITY(1,1) PRIMARY KEY,
    item_id              INT NOT NULL REFERENCES items(item_id),
    effective_from       DATE NOT NULL,
    effective_to         DATE NULL,
    material_cost        DECIMAL(12,4) NOT NULL,
    labour_cost          DECIMAL(12,4) NOT NULL,
    overhead_cost        DECIMAL(12,4) NOT NULL,
    cost_source          VARCHAR(20) NOT NULL, -- MEASURED / ASSUMED
    created_at           DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_standard_costs_item ON standard_costs(item_id);

-- ============================================================
-- State snapshots
-- ============================================================

CREATE TABLE inventory (
    inventory_id    INT IDENTITY(1,1) PRIMARY KEY,
    item_id         INT NOT NULL REFERENCES items(item_id),
    warehouse_id    INT NOT NULL REFERENCES warehouses(warehouse_id),
    snapshot_date   DATE NOT NULL,
    on_hand_qty     DECIMAL(14,2) NOT NULL, -- DQ: a few deliberately negative
    uom             VARCHAR(10) NOT NULL,
    created_at      DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_inventory_lookup ON inventory(item_id, warehouse_id, snapshot_date);

CREATE TABLE wip (
    wip_id              INT IDENTITY(1,1) PRIMARY KEY,
    production_order_id  INT NOT NULL REFERENCES production_orders(production_order_id),
    work_centre_id        INT NULL REFERENCES work_centres(work_centre_id),
    item_id               INT NOT NULL REFERENCES items(item_id),
    qty                   DECIMAL(14,2) NOT NULL,
    stage_entered_at      DATE NOT NULL,
    snapshot_date         DATE NOT NULL,
    created_at            DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_wip_lookup ON wip(work_centre_id, snapshot_date);
CREATE INDEX ix_wip_po ON wip(production_order_id);

-- ============================================================
-- Engine outputs (populated from CP2 onward; created now so the schema
-- is complete and stable for the whole build)
-- ============================================================

CREATE TABLE dq_findings (
    finding_id              INT IDENTITY(1,1) PRIMARY KEY,
    rule_id                  VARCHAR(60) NOT NULL,
    entity                   VARCHAR(60) NOT NULL,
    record_id                VARCHAR(60) NOT NULL,
    severity                 VARCHAR(20) NOT NULL,
    classification            VARCHAR(20) NOT NULL, -- TOLERABLE / ASSUMPTION_BASED / BLOCKING
    -- [CP2 req. 1/2] origin distinguishes a defect the synthetic generator
    -- deliberately planted (INJECTED, cross-referenced to the seeded
    -- staging/dq_issue_manifest.json via manifest_key) from one the rule
    -- engine discovered organically from relational/logical checks that are
    -- not tied to any specific planted defect (ORGANIC — e.g. BOM cycle
    -- detection, which nothing in the generator deliberately creates).
    origin                    VARCHAR(20) NOT NULL DEFAULT 'ORGANIC', -- INJECTED / ORGANIC
    manifest_key              VARCHAR(60) NULL, -- key into dq_issue_manifest.json when origin = INJECTED
    -- [CP3 req. 2] impact_scope: a BLOCKING finding does not, by default,
    -- invalidate the whole plant-wide analysis. GLOBAL_BLOCKING (reserved —
    -- no current rule triggers it) would; ENTITY_BLOCKING scopes to one
    -- specific entity (an item's BOM branch, an inventory record, a
    -- production order); KPI_BLOCKING scopes even narrower, to one specific
    -- calculated KPI for one entity-period (e.g. one work centre's
    -- utilization in one week), leaving that same entity's other KPIs and
    -- every other entity unaffected. NULL for non-BLOCKING findings, where
    -- the concept doesn't apply.
    impact_scope              VARCHAR(20) NULL, -- GLOBAL_BLOCKING / ENTITY_BLOCKING / KPI_BLOCKING
    affected_entity_type      VARCHAR(30) NULL, -- e.g. 'item', 'work_centre_period', 'production_order'
    affected_entity_id        VARCHAR(60) NULL, -- the id (or composite key) the scope applies to
    description               NVARCHAR(500) NOT NULL,
    detected_value            NVARCHAR(200) NULL,
    expected_constraint       NVARCHAR(200) NULL,
    recommended_action        NVARCHAR(500) NULL,
    detected_at               DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_dq_findings_entity ON dq_findings(entity, record_id);
CREATE INDEX ix_dq_findings_classification ON dq_findings(classification);
CREATE INDEX ix_dq_findings_origin ON dq_findings(origin, manifest_key);
CREATE INDEX ix_dq_findings_scope ON dq_findings(affected_entity_type, affected_entity_id, impact_scope);

CREATE TABLE scenario_runs (
    run_id              INT IDENTITY(1,1) PRIMARY KEY,
    name                 NVARCHAR(200) NOT NULL,
    parameters_json       NVARCHAR(MAX) NOT NULL,
    is_baseline           BIT NOT NULL DEFAULT 0,
    intervention_type     VARCHAR(20) NOT NULL DEFAULT 'NONE', -- NONE/BUFFER_ONLY/CAPACITY_ONLY/COMBINED
    seed                  INT NOT NULL,
    created_at            DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE material_requirements (
    material_requirement_id     INT IDENTITY(1,1) PRIMARY KEY,
    run_id                       INT NOT NULL REFERENCES scenario_runs(run_id),
    item_id                      INT NOT NULL REFERENCES items(item_id),
    period_start_date            DATE NOT NULL,
    gross_requirement            DECIMAL(14,2) NOT NULL,
    usable_inventory             DECIMAL(14,2) NOT NULL,
    scheduled_receipts           DECIMAL(14,2) NOT NULL,
    net_requirement              DECIMAL(14,2) NOT NULL,
    shortage_flag                BIT NOT NULL DEFAULT 0,
    provenance                   VARCHAR(20) NOT NULL, -- MEASURED / DERIVED / ASSUMED
    created_at                   DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_material_requirements_run ON material_requirements(run_id, item_id, period_start_date);

CREATE TABLE period_engine_results (
    period_result_id        INT IDENTITY(1,1) PRIMARY KEY,
    run_id                    INT NOT NULL REFERENCES scenario_runs(run_id),
    work_centre_id            INT NOT NULL REFERENCES work_centres(work_centre_id),
    period_start_date         DATE NOT NULL,
    calendar_hours             DECIMAL(10,2) NOT NULL,
    available_hours            DECIMAL(10,2) NOT NULL,
    effective_hours            DECIMAL(10,2) NOT NULL,
    required_hours             DECIMAL(10,2) NOT NULL,
    utilization_pct            DECIMAL(8,4) NOT NULL,
    backlog_hours_start        DECIMAL(10,2) NOT NULL,
    backlog_hours_end          DECIMAL(10,2) NOT NULL,
    wip_qty                    DECIMAL(14,2) NOT NULL,
    queue_time_days            DECIMAL(8,3) NOT NULL,
    constraint_classification  VARCHAR(20) NOT NULL DEFAULT 'NONE',
    created_at                 DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_period_engine_results_run ON period_engine_results(run_id, work_centre_id, period_start_date);

CREATE TABLE buffer_recommendations (
    buffer_recommendation_id    INT IDENTITY(1,1) PRIMARY KEY,
    run_id                       INT NOT NULL REFERENCES scenario_runs(run_id),
    item_id                      INT NOT NULL REFERENCES items(item_id),
    location_work_centre_id      INT NOT NULL REFERENCES work_centres(work_centre_id),
    recommended_min               DECIMAL(14,2) NOT NULL,
    recommended_max               DECIMAL(14,2) NOT NULL,
    reason                        NVARCHAR(1000) NOT NULL,
    inputs_json                   NVARCHAR(MAX) NOT NULL,
    confidence                    VARCHAR(20) NOT NULL, -- LOW / MEDIUM / HIGH
    assumptions                   NVARCHAR(1000) NOT NULL,
    created_at                    DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE INDEX ix_buffer_recommendations_run ON buffer_recommendations(run_id);
GO
