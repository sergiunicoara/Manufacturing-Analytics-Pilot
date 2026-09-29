-- Rerunnable: creates only what is missing. Never drops or rewrites data.
IF OBJECT_ID('dbo.cost_results', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.cost_results (
        cost_result_id    INT IDENTITY(1,1) PRIMARY KEY,
        run_id            INT NOT NULL REFERENCES dbo.scenario_runs(run_id),
        metric            VARCHAR(60) NOT NULL,
        value             DECIMAL(18,4) NULL,          -- NULL = unknown / unavailable, never zero
        units             VARCHAR(30) NOT NULL,
        provenance        VARCHAR(20) NOT NULL,        -- MEASURED / DERIVED / ASSUMED
        status            VARCHAR(20) NOT NULL,        -- COMPUTED / UNAVAILABLE
        assumptions_json  NVARCHAR(MAX) NOT NULL,
        created_at        DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT uq_cost_results_run_metric UNIQUE (run_id, metric)
    );
END
