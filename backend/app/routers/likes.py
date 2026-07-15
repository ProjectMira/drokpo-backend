from fastapi import APIRouter, Depends, Query

from app.dependencies import require_account_uid
from app.services import communityposts as communityposts_service
from app.services import news as news_service

router = APIRouter(prefix="/likes", tags=["likes"])


@router.get("/content")
def list_liked_content(uid: str = Depends(require_account_uid), limit: int = Query(default=100, le=200)):
    """Everything the member saved by liking content cards — news stories and
    community posts — merged newest-first by like time. People they liked stay
    on GET /api/swipes (the Likes tab merges the two client-side)."""
    items = [
        {"type": "news", "likedAt": item.get("likedAt"), "data": item}
        for item in news_service.list_liked(uid, limit)
    ] + [
        {"type": "communityPost", "likedAt": item.get("likedAt"), "data": item}
        for item in communityposts_service.list_liked(uid, limit)
    ]
    # likedAt is an ISO string on every item; missing values sort oldest.
    items.sort(key=lambda entry: entry.get("likedAt") or "", reverse=True)
    return {"items": items[:limit]}
