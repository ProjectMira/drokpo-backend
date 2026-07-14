from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_person_uid
from app.services import ads as ads_service
from app.services import communityposts as communityposts_service
from app.services import discover as discover_service
from app.services import news as news_service
from app.services import users as users_service

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("")
def get_feed(
    uid: str = Depends(require_person_uid),
    limit: int = Query(default=20, le=50),
    shape: str = Query(default="legacy"),
):
    profile = users_service.get_profile(uid)
    if not profile or not profile.get("onboardingComplete"):
        raise HTTPException(status_code=400, detail="Complete onboarding before viewing the feed")
    candidates = users_service.get_candidates(uid, profile, limit)
    ads = ads_service.list_active()
    news = news_service.list_active()
    # The cached posts are viewer-agnostic; overlay this caller's poll
    # votes and event RSVPs so the deck doesn't forget them.
    posts = communityposts_service.attach_viewer_state(
        uid, communityposts_service.list_active_for_feed()
    )
    if shape == "items":
        # Server-ordered deck: mixing (and the never-empty top-up) happens
        # here, so the policy can change without an app release.
        return {"items": discover_service.build_items(candidates, ads, news, posts)}
    # Legacy shape — the shipped app build interleaves these client-side.
    return {"candidates": candidates, "ads": ads, "news": news, "communityPosts": posts}
