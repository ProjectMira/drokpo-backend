from fastapi import APIRouter, FastAPI

from app.routers import feed, matches, onboarding, profile, reports, swipes

app = FastAPI(title="Changsa API")

# Firebase Hosting rewrites /api/** to this service and forwards the path
# unstripped, so every route must live under /api to be reachable through
# the Hosting domain.
api = APIRouter(prefix="/api")
api.include_router(onboarding.router)
api.include_router(profile.router)
api.include_router(feed.router)
api.include_router(swipes.router)
api.include_router(matches.router)
api.include_router(reports.router)
app.include_router(api)


@app.get("/health")
def health():
    # Root-level for Cloud Run probes hitting the service URL directly.
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    # Same check, reachable through the Hosting /api/** rewrite.
    return {"status": "ok"}
