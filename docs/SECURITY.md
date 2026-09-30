# Security and data protection

The pilot has two profiles. **demo** runs locally on synthetic data only and is deliberately permissive. **secured** is the configuration to use for any client data. A Docker configuration or a written runbook is **not** evidence of an EU deployment or of legal compliance. Those need the client's infrastructure and approvals, as listed at the end of this page.

## Controls, by evidence level

| Control | Implemented | Tested (how) | Needs client / infrastructure |
|---|---|---|---|
| **Secured profile refuses unsafe start-up:** default password, `sa` login, missing API keys, keys under 24 characters, no CORS origins | `app/security.py::validate_settings`, `main.py` | `tests/test_security.py`; live: import with defaults raised `RuntimeError` listing all five problems | — |
| **API authentication:** reader key for every data route, operator key for `POST /api/scenarios/run` and `/copilot/ask`; constant-time comparison; keys never echoed; `/docs`, `/redoc` and `/openapi.json` are disabled in the secured profile (they would be unauthenticated) | `app/security.py`, router and route dependencies | Unit tests (401/403 matrix, route introspection). Live secured instance: no key 401, wrong key 401, reader GET 200, reader POST 403, reader copilot 403, operator copilot 200 | SSO / identity provider for named users |
| **No browser credentials in bundles:** the secured compose override does not start the frontend | `docker-compose.secured.yml` | `docker compose ... config --services` lists only `sqlserver`, `api` | Authenticating reverse proxy or SSO in front of a UI |
| **Local ports bound to 127.0.0.1** in both profiles (demo and secured) | `docker-compose.yml`, `docker-compose.secured.yml` | `docker compose config`, and `docker ps` on the running stacks, show `127.0.0.1` for 1433, 8000 and 5173 | Network segmentation on the real host |
| **Secrets never in Git or logs:** `.env` ignored; compose requires `${VAR:?}`; logins created from environment variables and passed as query parameters, quoted with `QUOTENAME` | `.gitignore`, `app/db/security_setup.py` | Live: API keys found 0 times in the secured instance's log | Secret store (vault) on the host |
| **Least-privilege SQL identities** | `db/migrations/200_security_roles.sql`, `app/db/security_setup.py` | `tests/test_least_privilege.py` (live, passed): `pilot_reader` reads `vw_*` only and is denied base tables and deletes. `pilot_app` reads sources and writes only its 7 result tables; denied DDL, `DROP`, source writes and role changes. The secured API ran its full context build as `pilot_app` | Client DBA review; separate extraction login on the client side |
| **External LLM only by explicit opt-in** (`ALLOW_EXTERNAL_LLM=true` plus a key) in the secured profile; deterministic evidence-only by default | `security.llm_allowed` | Unit test: with evidence and a key but no opt-in, the provider is never called; existing test: no call without evidence | Client authorisation to send any data to a provider |
| **Payload minimisation** before an LLM call: an allow-list of numeric, label and method fields (`security.LLM_ALLOWED_KEYS`); record ids, descriptions, detected values and free-text assumptions are never sent; lists capped at 20 | `security.minimise_for_llm` | Unit tests (`test_security.py`, `test_review_fixes.py`) | — |
| **Safe restore:** validated names and paths, refuses to overwrite, copy-only backups, never `WITH REPLACE` | `app/db/restore.py` | `tests/test_restore_safety.py` (16), including an injection attempt refused; live round trip into a new database | Restore on the approved host |
| **Dependency checks:** `pip-audit` and `npm audit` (commands in OPERATIONS.md); pins raised 2026-09-30 after `pip-audit` flagged `starlette`, `pytest` and `python-dotenv` | `backend/requirements.txt` | Both audits clean on 2026-09-30; full suite passed on the new pins | Periodic re-run; a pin is a snapshot |
| **Read-only toward the ERP;** parameter export has `erp_write_back: false` | `parameter_export.py` | Schema test rejects `erp_write_back: true` | — |

## Starting the secured profile

Every value below must come from the environment or a secret store; `docker-compose.secured.yml` has no defaults and refuses to start without them. Docker Compose interpolates the whole file, so `MSSQL_SA_PASSWORD` is required even when only the API is restarted; the API itself never receives it.

```bash
# set these in the shell (values from a password manager; never commit them):
#   MSSQL_SA_PASSWORD, PILOT_APP_PASSWORD, PILOT_READER_API_KEYS, PILOT_OPERATOR_API_KEYS (each key >= 24 characters), ALLOWED_ORIGINS
docker compose -f docker-compose.yml -f docker-compose.secured.yml up -d sqlserver api
docker exec -e PILOT_READER_PASSWORD -e PILOT_APP_PASSWORD -w /app mfg_pilot_api python -m app.db.security_setup   # as administrator; creates the logins, or rotates their passwords
```

Clients send `X-API-Key: <key>`. To return to the demo profile, run `docker compose up -d --no-deps api` without the override.

**Verified live on 2026-09-30** by running the real override (not a scratch instance): the API started with `APP_PROFILE=secured`, the `pilot_app` login, an empty SA password and the external-LLM opt-in off; `/health` without a key 200; data routes without a key or with a wrong key 401; a reader key 200 on pages, the DQ export (all 641 findings) and the Executive Story; a reader key 403 on `POST /api/scenarios/run` and `POST /copilot/ask`; an operator key 200 on the copilot with `llm_used: false`; a cross-origin preflight from an unlisted origin got no allow-origin header; the warm-up and the reference-case build completed under the least-privilege login; the secured log had no API keys and no errors or permission denials.

`security_setup` rejects passwords longer than SQL Server's 128-character limit instead of truncating them, and re-running it with new values rotates the passwords of existing logins.

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
- The demo profile's open CORS, `sa` login and default password are for local synthetic data only. Its ports are published on `127.0.0.1`, so other machines on the network cannot reach it.
- The live database tests skip when SQL Server or the two logins are unavailable; run them with `REQUIRE_DB_TESTS=1` (see OPERATIONS.md) to make a skip a failure.
