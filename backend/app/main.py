from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware

from app.routers import (
    account,
    ads,
    communities,
    feed,
    matches,
    messages,
    news,
    onboarding,
    posts,
    profile,
    reports,
    swipes,
)

app = FastAPI(title="Drokpo API")

# Feed/matches pages are tens of KB of highly compressible JSON; gzip cuts
# them ~5-10x on the cell networks most members are on.
app.add_middleware(GZipMiddleware, minimum_size=1024)


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
api.include_router(account.router)
api.include_router(onboarding.router)
api.include_router(profile.router)
api.include_router(communities.router)
api.include_router(posts.router)
api.include_router(feed.router)
api.include_router(swipes.router)
api.include_router(matches.router)
api.include_router(messages.router)
api.include_router(reports.router)
api.include_router(ads.router)
api.include_router(news.router)
app.include_router(api)


@app.get("/health")
def health():
    # Root-level for Cloud Run probes hitting the service URL directly.
    return {"status": "ok"}


@app.get("/api/health")
def api_health():
    # Same check, reachable through the Hosting /api/** rewrite.
    return {"status": "ok"}
