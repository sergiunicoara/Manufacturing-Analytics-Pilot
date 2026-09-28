-- Existing databases: unknown KPI-blocked capacity is NULL, never zero.
ALTER TABLE period_engine_results ALTER COLUMN calendar_hours DECIMAL(10,2) NULL;
ALTER TABLE period_engine_results ALTER COLUMN available_hours DECIMAL(10,2) NULL;
ALTER TABLE period_engine_results ALTER COLUMN effective_hours DECIMAL(10,2) NULL;
ALTER TABLE period_engine_results ALTER COLUMN required_hours DECIMAL(10,2) NULL;
ALTER TABLE period_engine_results ALTER COLUMN utilization_pct DECIMAL(8,4) NULL;
ALTER TABLE period_engine_results ALTER COLUMN queue_time_days DECIMAL(8,3) NULL;
