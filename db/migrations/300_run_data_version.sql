-- Rerunnable. Identity of the source data + analytical code behind each persisted run.
-- NULL on runs created before this migration: they are never reused, but stay as audit history.
IF COL_LENGTH('dbo.scenario_runs', 'data_version') IS NULL
    ALTER TABLE dbo.scenario_runs ADD data_version VARCHAR(80) NULL;
GO
