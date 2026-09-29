-- Versioned, rerunnable T-SQL read models for BI (Qlik Cloud / Bright Analytics) and audit.
-- One authoritative definition per metric. The Python functions named beside each view compute the
-- same metric; tests/test_sql_read_models.py asserts parity against a running SQL Server.
-- CREATE OR ALTER only: re-applying changes definitions, never data.

-- Grain: one row per forecast revision (forecast_id).
-- Python twin: app.analytics.forecast.reconstruct_forecast_history
-- actual_qty = order-line quantity for the same customer, item and site whose requested_ship_date
--              equals the forecast's delivery_period_start (exact date match, as in Python).
-- horizon_weeks = ROUND(days between snapshot and delivery / 7). Units: quantities in the item base UoM.
CREATE OR ALTER VIEW dbo.vw_forecast_revisions AS
WITH actuals AS (
    SELECT so.customer_id, sol.item_id, so.site_id, sol.requested_ship_date AS delivery_period_start,
           SUM(sol.qty) AS actual_qty
    FROM dbo.sales_order_lines AS sol
    JOIN dbo.sales_orders AS so ON so.sales_order_id = sol.sales_order_id
    GROUP BY so.customer_id, sol.item_id, so.site_id, sol.requested_ship_date
)
SELECT cf.forecast_id, cf.forecast_version_id, fv.snapshot_date, cf.delivery_period_start,
       cf.customer_id, cf.item_id, cf.site_id,
       CAST(cf.qty AS DECIMAL(18,4)) AS forecast_qty,
       CAST(COALESCE(a.actual_qty, 0) AS DECIMAL(18,4)) AS actual_qty,
       CAST(ROUND(DATEDIFF(DAY, fv.snapshot_date, cf.delivery_period_start) / 7.0, 0) AS INT) AS horizon_weeks
FROM dbo.customer_forecasts AS cf
JOIN dbo.forecast_versions AS fv ON fv.forecast_version_id = cf.forecast_version_id
LEFT JOIN actuals AS a
       ON a.customer_id = cf.customer_id AND a.item_id = cf.item_id AND a.site_id = cf.site_id
      AND a.delivery_period_start = cf.delivery_period_start;
GO

-- Grain: one row per horizon bucket. Python twin: app.analytics.forecast.compute_accuracy_by_horizon
-- WAPE = SUM(|forecast - actual|) / SUM(actual); NULL when SUM(actual) = 0. Revisions outside the
-- defined buckets (negative horizon) are excluded, as in Python.
CREATE OR ALTER VIEW dbo.vw_forecast_accuracy_by_horizon AS
WITH bucketed AS (
    SELECT r.*,
           CASE WHEN horizon_weeks BETWEEN 0 AND 2 THEN '0-2w'
                WHEN horizon_weeks BETWEEN 3 AND 4 THEN '3-4w'
                WHEN horizon_weeks BETWEEN 5 AND 8 THEN '5-8w'
                WHEN horizon_weeks BETWEEN 9 AND 12 THEN '9-12w'
                WHEN horizon_weeks BETWEEN 13 AND 20 THEN '13-20w'
                WHEN horizon_weeks >= 21 THEN '21w+' END AS horizon_bucket,
           CASE WHEN horizon_weeks BETWEEN 0 AND 2 THEN 1 WHEN horizon_weeks BETWEEN 3 AND 4 THEN 2
                WHEN horizon_weeks BETWEEN 5 AND 8 THEN 3 WHEN horizon_weeks BETWEEN 9 AND 12 THEN 4
                WHEN horizon_weeks BETWEEN 13 AND 20 THEN 5 WHEN horizon_weeks >= 21 THEN 6 END AS bucket_order
    FROM dbo.vw_forecast_revisions AS r
)
SELECT horizon_bucket, bucket_order,
       COUNT(*) AS n_observations,
       CAST(SUM(actual_qty) AS DECIMAL(18,4)) AS sum_actual_qty,
       CAST(AVG(ABS(forecast_qty - actual_qty)) AS DECIMAL(18,6)) AS mae,
       CAST(SUM(ABS(forecast_qty - actual_qty)) / NULLIF(SUM(actual_qty), 0) AS DECIMAL(18,6)) AS wape,
       CAST(SUM(forecast_qty - actual_qty) / NULLIF(SUM(actual_qty), 0) AS DECIMAL(18,6)) AS bias
FROM bucketed
WHERE horizon_bucket IS NOT NULL
GROUP BY horizon_bucket, bucket_order;
GO

-- Grain: one row per persisted run × work centre × week. Source: period_engine_results (engine output,
-- written by scenario_store.save_run). NULL capacity values are KPI-blocked unknowns, not zero.
-- Units: hours; utilization_pct is a ratio (1.0 = 100 %).
CREATE OR ALTER VIEW dbo.vw_weekly_load AS
SELECT sr.run_id, sr.name AS scenario, sr.seed, per.period_start_date, per.work_centre_id,
       wc.work_centre_code, wc.name AS work_centre, wc.process_type,
       per.calendar_hours, per.available_hours, per.effective_hours, per.required_hours,
       per.utilization_pct, per.backlog_hours_start, per.backlog_hours_end, per.queue_time_days,
       per.constraint_classification
FROM dbo.period_engine_results AS per
JOIN dbo.scenario_runs AS sr ON sr.run_id = per.run_id
JOIN dbo.work_centres AS wc ON wc.work_centre_id = per.work_centre_id;
GO

-- Grain: one row per operation with a usable recorded elapsed time (record class COMPLETED_VALID).
-- Python twin: app.analytics.stage_history.classify_operations. Excluded, as in Python: duplicates of
-- (production_order_id, seq_no) after the first, orders with an ENTITY_BLOCKING DQ finding, missing start
-- or finish, finish before start, and non-COMPLETED status. Elapsed time is calendar elapsed time from
-- recorded timestamps; it is not productive processing time. Data origin in this pilot: SYNTHETIC.
CREATE OR ALTER VIEW dbo.vw_stage_elapsed AS
WITH ranked AS (
    SELECT poo.*, ROW_NUMBER() OVER (PARTITION BY poo.production_order_id, poo.seq_no
                                     ORDER BY poo.po_operation_id) AS dup_rank
    FROM dbo.production_order_operations AS poo
), blocked_orders AS (
    SELECT DISTINCT TRY_CAST(affected_entity_id AS INT) AS production_order_id
    FROM dbo.dq_findings
    WHERE classification = 'BLOCKING' AND impact_scope = 'ENTITY_BLOCKING'
      AND affected_entity_type = 'production_order'
)
SELECT r.po_operation_id, r.production_order_id, r.seq_no, r.work_centre_id, po.item_id, po.site_id,
       r.actual_start, r.actual_finish,
       CAST(DATEDIFF(SECOND, r.actual_start, r.actual_finish) / 3600.0 AS DECIMAL(18,4)) AS elapsed_hours
FROM ranked AS r
JOIN dbo.production_orders AS po ON po.production_order_id = r.production_order_id
WHERE r.dup_rank = 1
  AND r.status = 'COMPLETED'
  AND r.actual_start IS NOT NULL AND r.actual_finish IS NOT NULL
  AND r.actual_finish >= r.actual_start
  AND r.production_order_id NOT IN (SELECT production_order_id FROM blocked_orders WHERE production_order_id IS NOT NULL);
GO

-- Grain: one row per work centre. Python twin: app.analytics.stage_history.summarize_elapsed
-- (count and continuous percentiles; pandas' default linear quantile equals PERCENTILE_CONT).
CREATE OR ALTER VIEW dbo.vw_stage_elapsed_by_work_centre AS
SELECT DISTINCT work_centre_id,
       COUNT(*) OVER (PARTITION BY work_centre_id) AS n_observations,
       CAST(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY elapsed_hours) OVER (PARTITION BY work_centre_id) AS DECIMAL(18,4)) AS p50_hours,
       CAST(PERCENTILE_CONT(0.85) WITHIN GROUP (ORDER BY elapsed_hours) OVER (PARTITION BY work_centre_id) AS DECIMAL(18,4)) AS p85_hours,
       CAST(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY elapsed_hours) OVER (PARTITION BY work_centre_id) AS DECIMAL(18,4)) AS p95_hours
FROM dbo.vw_stage_elapsed;
GO

-- Grain: one row per persisted run × cost metric. NULL value with status UNAVAILABLE = not computable.
CREATE OR ALTER VIEW dbo.vw_cost_results AS
SELECT sr.run_id, sr.name AS scenario, cr.metric, cr.value, cr.units, cr.provenance, cr.status,
       cr.assumptions_json, cr.created_at
FROM dbo.cost_results AS cr
JOIN dbo.scenario_runs AS sr ON sr.run_id = cr.run_id;
GO
