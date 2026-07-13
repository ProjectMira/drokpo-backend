from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_person_uid
from app.services import ads as ads_service
from app.services import communityposts as communityposts_service
from app.services import news as news_service
from app.services import users as users_service

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("")
def get_feed(uid: str = Depends(require_person_uid), limit: int = Query(default=20, le=50)):
    profile = users_service.get_profile(uid)
    if not profile or not profile.get("onboardingComplete"):
        raise HTTPException(status_code=400, detail="Complete onboarding before viewing the feed")
    candidates = users_service.get_candidates(uid, profile, limit)
    # Sponsored cards, news, and community posts all ride along with every
    # page; the client interleaves one content card after every few real
    # profiles in the Discover deck, rotating across these three queues.
    return {
        "candidates": candidates,
        "ads": ads_service.list_active(),
        "news": news_service.list_active(),
        "communityPosts": communityposts_service.list_active_for_feed(),
    }
