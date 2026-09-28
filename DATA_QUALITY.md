# Data quality

The generator deliberately introduces a limited set of defects alongside organic defects. `backend/app/dq/rules.py` registers the checks; `run_all` produces classified findings. The dashboard shows rule, source entity, severity, classification, origin, and affected scope through evidence.

- **Tolerable:** reported but can remain in a calculation.
- **Assumption based:** usable only with a stated analytical assumption.
- **Blocking:** excluded at the declared global, entity, or KPI scope.

The BOM explosion records incomplete branches for ambiguous effective revisions, cycles, and missing components. The planning core uses the blocking index to avoid treating flagged records as valid inputs. This protects an unrelated valid item or work centre from being discarded when a defect is local. Findings are diagnostic; they do not silently repair source data.
