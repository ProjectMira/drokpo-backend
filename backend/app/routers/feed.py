from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_account_uid
from app.firebase import get_firestore
from app.services import ads as ads_service
from app.services import communities as communities_service
from app.services import communityposts as communityposts_service
from app.services import discover as discover_service
from app.services import news as news_service
from app.services import users as users_service

router = APIRouter(prefix="/feed", tags=["feed"])


@router.get("")
def get_feed(
    uid: str = Depends(require_account_uid),
    limit: int = Query(default=20, le=50),
    shape: str = Query(default="legacy"),
):
    profile = users_service.get_profile(uid)
    if profile:
        if not profile.get("onboardingComplete"):
            raise HTTPException(status_code=400, detail="Complete onboarding before viewing the feed")
        candidates = users_service.get_candidates(uid, profile, limit)
    else:
        # Community viewer: never onboarding-gated (no person onboarding to
        # complete), and only a verified community sees person candidates at
        # all — mirrors the verified-only gate on liking people.
        community = communities_service.get_community(uid)
        is_verified = bool(community) and community.get("verification") == "verified"
        candidates = users_service.get_candidates_for_community(uid, limit) if is_verified else []

    ads = ads_service.list_active()
    # Already-liked content never cycles back into the deck — a saved story or
    # post lives in the Likes tab; re-serving it reads as a broken feed.
    liked_news = news_service.liked_ids(uid)
    news = [n for n in news_service.list_active() if n["newsId"] not in liked_news]
    liked_posts = communityposts_service.liked_ids(uid)
    posts = [p for p in communityposts_service.list_active_for_feed() if p["postId"] not in liked_posts]
    # Blocking a community hides its posts too, not just its account card.
    blocked = users_service._blocked_uids(get_firestore(), uid)
    if blocked:
        posts = [p for p in posts if p.get("communityId") not in blocked]
    # The cached posts are viewer-agnostic; overlay this caller's poll
    # votes and event RSVPs so the deck doesn't forget them.
    posts = communityposts_service.attach_viewer_state(uid, posts)
    if profile is None:
        # A community never sees its own posts in its own Discover feed.
        # Filter the (already-copied, per-viewer) list — never the shared cache.
        posts = [p for p in posts if p.get("communityId") != uid]
    if shape == "items":
        # Server-ordered deck: mixing (and the never-empty top-up) happens
        # here, so the policy can change without an app release.
        return {"items": discover_service.build_items(candidates, ads, news, posts)}
    # Legacy shape — the shipped app build interleaves these client-side.
    return {"candidates": candidates, "ads": ads, "news": news, "communityPosts": posts}
