# Security and data protection

The pilot has two profiles. **demo** runs locally on synthetic data only and is deliberately permissive. **secured** is the configuration to use for any client data. A Docker configuration or a written runbook is **not** evidence of an EU deployment or of legal compliance. Those need the client's infrastructure and approvals, as listed at the end of this page.

## Controls, by evidence level

| Control | Implemented | Tested (how) | Needs client / infrastructure |
|---|---|---|---|
| **Secured profile refuses unsafe start-up:** default password, `sa` login, missing API keys, keys under 24 characters, no CORS origins | `app/security.py::validate_settings`, `main.py` | `tests/test_security.py`; live: import with defaults raised `RuntimeError` listing all five problems | — |
| **API authentication:** reader key for every data route, operator key for `POST /api/scenarios/run` and `/copilot/ask`; constant-time comparison; keys never echoed | `app/security.py`, router and route dependencies | Unit tests (401/403 matrix, route introspection). Live secured instance: no key 401, wrong key 401, reader GET 200, reader POST 403, reader copilot 403, operator copilot 200 | SSO / identity provider for named users |
| **No browser credentials in bundles:** the secured compose override does not start the frontend | `docker-compose.secured.yml` | `docker compose ... config --services` lists only `sqlserver`, `api` | Authenticating reverse proxy or SSO in front of a UI |
| **Local ports bound to 127.0.0.1** in the secured profile | `docker-compose.secured.yml` | `docker compose config` shows `host_ip: 127.0.0.1` for 1433 and 8000 | Network segmentation on the real host |
| **Secrets never in Git or logs:** `.env` ignored; compose requires `${VAR:?}`; logins created from environment variables and passed as query parameters, quoted with `QUOTENAME` | `.gitignore`, `app/db/security_setup.py` | Live: API keys found 0 times in the secured instance's log | Secret store (vault) on the host |
| **Least-privilege SQL identities** | `db/migrations/200_security_roles.sql`, `app/db/security_setup.py` | `tests/test_least_privilege.py` (live, passed): `pilot_reader` reads `vw_*` only and is denied base tables and deletes. `pilot_app` reads sources and writes only its 7 result tables; denied DDL, `DROP`, source writes and role changes. The secured API ran its full context build as `pilot_app` | Client DBA review; separate extraction login on the client side |
| **External LLM only by explicit opt-in** (`ALLOW_EXTERNAL_LLM=true` plus a key) in the secured profile; deterministic evidence-only by default | `security.llm_allowed` | Unit test: with evidence and a key but no opt-in, the provider is never called; existing test: no call without evidence | Client authorisation to send any data to a provider |
| **Payload minimisation** before an LLM call: record, customer and entity identifiers removed; lists capped at 20 | `security.minimise_for_llm` | Unit test | — |
| **Safe restore:** validated names and paths, refuses to overwrite, copy-only backups, never `WITH REPLACE` | `app/db/restore.py` | `tests/test_restore_safety.py` (16), including an injection attempt refused; live round trip into a new database | Restore on the approved host |
| **Read-only toward the ERP;** parameter export has `erp_write_back: false` | `parameter_export.py` | Schema test rejects `erp_write_back: true` | — |

## What leaves the process

| Destination | When | Content |
|---|---|---|
| Browser / API client | Any authorised request | Read models and evidence for the requested page |
| BI files (`FileBIExportAdapter`) | Explicit export | CSV plus a provenance sidecar |
| Anthropic API | Demo profile with a key set, or secured profile with `ALLOW_EXTERNAL_LLM=true` and a key | The question plus the minimised deterministic evidence bundle (secured profile). Nothing is sent when evidence is missing |

**EU processing of the LLM call is not assumed.** Setting a region string does not guarantee where a provider processes data. No provider deployment's EU-only processing has been verified in this session, so for client data the secured default (external explanations off) must stay in place. Change that only after checking the provider's current, authoritative documentation and agreeing it with the client.

## EU hosting and data protection: requirements for the client deployment

These are requirements, not implemented facts:

- **Residency:** the landing database, backups and application host must be in an EU region the client approves. Record the provider, region and account owner.
- **Encryption in transit:** TLS to SQL Server with a trusted certificate. The local stack uses `TrustServerCertificate=yes` and is not acceptable for client data. HTTPS in front of the API.
- **Encryption at rest:** TDE or encrypted volumes for the database. Encrypted backups, with the key held by the client.
- **Pseudonymisation:** customer names and any personal data (contact names, e-mail addresses) are removed or pseudonymised in the landing database before the pilot reads it. The analytics only need stable customer codes.
- **Retention:** client data and backups are deleted at pilot end, on a date agreed in advance and confirmed in writing. Disposable restore databases are deleted as soon as they have been validated. This tool never deletes; deletion is a separate, documented action.
- **Audit access:** named accounts only, no shared logins. SQL Server audit or login auditing is enabled on the client host. API access logs are kept for the agreed period, without keys or evidence payloads.
- **Incident responsibilities:** the client's security contact and the pilot engineer agree a notification path and timeline before data arrives. The pilot engineer reports any suspected exposure immediately.
- **Contract:** a data processing agreement covering the pilot is in place before any real data is transferred.

## Not in scope, or not demonstrated

- Deployment to an EU host, TLS certificates, TDE and a vault. These are infrastructure work the client must approve.
- Named-user authorization (SSO / RBAC per person). The API keys are service-level credentials.
- The demo profile's open CORS, `sa` login and default password are for local synthetic data only.
