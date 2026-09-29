-- Least-privilege database roles. Rerunnable. Logins and passwords are NOT created here (no secrets in
-- files); app.db.security_setup creates them from environment variables and adds users to these roles.
--
-- pilot_reader_role : BI / extraction consumers. SELECT on the vw_* read models only; no base tables.
-- pilot_app_role    : the API process. Reads source tables; writes only its own result tables. No DDL,
--                     no access to schema_migrations writes, no ability to grant.
IF DATABASE_PRINCIPAL_ID('pilot_reader_role') IS NULL CREATE ROLE pilot_reader_role;
GO
IF DATABASE_PRINCIPAL_ID('pilot_app_role') IS NULL CREATE ROLE pilot_app_role;
GO
GRANT SELECT ON dbo.vw_forecast_revisions TO pilot_reader_role;
GRANT SELECT ON dbo.vw_forecast_accuracy_by_horizon TO pilot_reader_role;
GRANT SELECT ON dbo.vw_weekly_load TO pilot_reader_role;
GRANT SELECT ON dbo.vw_stage_elapsed TO pilot_reader_role;
GRANT SELECT ON dbo.vw_stage_elapsed_by_work_centre TO pilot_reader_role;
GRANT SELECT ON dbo.vw_cost_results TO pilot_reader_role;
GO
GRANT SELECT ON SCHEMA::dbo TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.scenario_runs TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.period_engine_results TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.material_requirements TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.buffer_recommendations TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.cost_results TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.dq_findings TO pilot_app_role;
GRANT INSERT, UPDATE, DELETE ON dbo.forecast_consumption TO pilot_app_role;
GO
