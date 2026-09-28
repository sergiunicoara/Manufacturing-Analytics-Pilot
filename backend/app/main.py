"""FastAPI entrypoint.

CP1 scope: a bare health check only, so `docker compose up` brings up a
working `api` service. The analytics endpoints (Evidence Drawer payloads,
scenario runs, copilot, etc.) are added from CP4 onward per PLAN.md.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Manufacturing Analytics Pilot API",
    description="Synthetic-data manufacturing analytics pilot. All data is "
    "fictional; this is not connected to any real ERP/MES/BI system.",
)

# Local-only synthetic demo, not a production deployment — wide open CORS is
# fine here and avoids hardcoding the frontend dev-server port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
