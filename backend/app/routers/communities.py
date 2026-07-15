from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_uid, require_community_uid, require_person_uid
from app.models.community import (
    CommunityOnboardingIn,
    CommunityPhotoConfirm,
    CommunityPhotoOrderIn,
    CommunityUpdate,
)
from app.models.community_post import CommunityPostIn, CommunityPostUpdate
from app.routers.common import attach_community_photo, require_owned_community_photo_path
from app.services import communities as communities_service
from app.services import communityposts as communityposts_service
from app.services import storage as storage_service
from app.services import users as users_service

router = APIRouter(prefix="/communities", tags=["communities"])

# Route order matters below: FastAPI/Starlette tries routes in registration
# order and a GET on a literal path like "/mine" would otherwise be captured
# by the "/{cid}" wildcard if that were registered first. Every fixed-segment
# route (me, mine, me/posts, ...) is declared before "/{cid}" and "/{cid}/...".


@router.post("/onboarding")
def create_community(payload: CommunityOnboardingIn, uid: str = Depends(get_current_uid)):
    if users_service.get_profile(uid):
        raise HTTPException(status_code=409, detail="This account is already registered as a person")
    if communities_service.community_exists(uid):
        raise HTTPException(status_code=409, detail="Community already exists")
    communities_service.create_community(uid, payload)
    return {"uid": uid}


@router.get("/me")
def get_my_community(uid: str = Depends(require_community_uid)):
    community = communities_service.get_community(uid)
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    return community


@router.delete("/me")
def delete_my_community(uid: str = Depends(require_community_uid)):
    communities_service.delete_community(uid)
    return {"ok": True}


@router.patch("/me")
def update_my_community(payload: CommunityUpdate, uid: str = Depends(require_community_uid)):
    communities_service.update_community(uid, payload)
    return {"ok": True}


@router.post("/me/photos")
def add_photo(payload: CommunityPhotoConfirm, uid: str = Depends(require_community_uid)):
    attach_community_photo(uid, payload)
    return {"ok": True}


@router.patch("/me/photos/order")
def reorder_photos(payload: CommunityPhotoOrderIn, uid: str = Depends(require_community_uid)):
    try:
        communities_service.reorder_photos(uid, payload.storagePaths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/me/photos")
def delete_photo(storage_path: str, uid: str = Depends(require_community_uid)):
    require_owned_community_photo_path(uid, storage_path, "delete")
    communities_service.remove_photo(uid, storage_path)
    storage_service.delete_blob(storage_path)
    return {"ok": True}


@router.post("/me/posts")
def create_my_post(payload: CommunityPostIn, uid: str = Depends(require_community_uid)):
    try:
        post_id = communityposts_service.create_post(uid, payload)
    except communityposts_service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"postId": post_id}


@router.patch("/me/posts/{post_id}")
def update_my_post(post_id: str, payload: CommunityPostUpdate, uid: str = Depends(require_community_uid)):
    try:
        communityposts_service.update_post(uid, post_id, payload)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/mine")
def list_my_communities(uid: str = Depends(get_current_uid)):
    return {"communities": communities_service.list_my_communities(uid)}


@router.get("/feed")
def get_joined_communities_feed(
    uid: str = Depends(require_person_uid), limit: int = Query(default=30, le=50)
):
    return {"posts": communityposts_service.list_feed_for_member(uid, limit)}


@router.get("/home")
def get_communities_home(
    uid: str = Depends(require_person_uid), limit: int = Query(default=30, le=50)
):
    """One call for the person-side Communities screen: the joined-communities
    rail plus a typed feed of their posts with sponsored cards interleaved."""
    from app.services import ads as ads_service
    from app.services import discover as discover_service

    return {
        "communities": communities_service.list_my_communities(uid),
        "items": discover_service.interleave_posts_with_ads(
            communityposts_service.list_feed_for_member(uid, limit),
            ads_service.list_active(),
        ),
    }


@router.get("")
def list_communities(uid: str = Depends(get_current_uid), limit: int = Query(default=50, le=50)):
    return {"communities": communities_service.list_directory(uid, limit)}


@router.get("/{cid}")
def get_community(cid: str, uid: str = Depends(get_current_uid)):
    card = communities_service.get_community_card(uid, cid)
    if not card:
        raise HTTPException(status_code=404, detail="Community not found")
    return card


@router.get("/{cid}/posts")
def list_community_posts(
    cid: str,
    uid: str = Depends(get_current_uid),
    limit: int = Query(default=20, le=50),
    before: str | None = Query(default=None, description="Post ID to page back from"),
):
    return {"posts": communityposts_service.list_posts(cid, uid, limit, before)}


@router.get("/{cid}/members")
def list_community_members(
    cid: str, uid: str = Depends(get_current_uid), limit: int = Query(default=50, le=100)
):
    # Deliberately not require_person_uid/require_community_uid: either role
    # may pass here — the real check is "member of cid, or cid itself".
    if not communities_service.is_member_or_self(uid, cid):
        raise HTTPException(status_code=403, detail="Only members can view this community's member list")
    return {"members": communities_service.list_members(cid, limit)}


@router.post("/{cid}/join")
def join_community(cid: str, uid: str = Depends(require_person_uid)):
    try:
        communities_service.join_community(cid, uid)
    except communities_service.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/{cid}/join")
def leave_community(cid: str, uid: str = Depends(require_person_uid)):
    communities_service.leave_community(cid, uid)
    return {"ok": True}
