from fastapi import APIRouter, FastAPI, Request

from app.routers import feed, matches, messages, onboarding, profile, reports, swipes

app = FastAPI(title="Drokpo API")


@app.middleware("http")
async def no_store(request: Request, call_next):
    # Authenticated, per-user JSON must never be cached. Without this header,
    # URLSession's cache heuristics may replay stale responses — a cached 404
    # for GET /profile/me once trapped freshly-onboarded users on the
    # onboarding screen.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

# Firebase Hosting rewrites /api/** to this service and forwards the path
# unstripped, so every route must live under /api to be reachable through
# the Hosting domain.
api = APIRouter(prefix="/api")
api.include_router(onboarding.router)
api.include_router(profile.router)
api.include_router(feed.router)
api.include_router(swipes.router)
api.include_router(matches.router)
api.include_router(messages.router)
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
