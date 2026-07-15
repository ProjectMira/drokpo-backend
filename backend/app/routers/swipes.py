from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_account_uid
from app.models.swipe import SwipeIn
from app.services import communities as communities_service
from app.services import matching as matching_service
from app.services import users as users_service
from app.services.matching import BlockedError, MatchedError

router = APIRouter(prefix="/swipes", tags=["swipes"])


@router.get("")
def list_swipes(
    uid: str = Depends(require_account_uid),
    action: Literal["like", "pass", "superlike"] | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    # e.g. GET /api/swipes?action=like returns every like the caller has sent.
    return {"swipes": matching_service.list_swipes(uid, action, limit)}


@router.get("/received")
def list_received_swipes(
    uid: str = Depends(require_account_uid),
    action: Literal["like", "pass", "superlike"] | None = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    # e.g. GET /api/swipes/received?action=like returns every like the caller has received.
    return {"swipes": matching_service.list_received(uid, action, limit)}


@router.post("/{target_uid}")
def swipe(target_uid: str, payload: SwipeIn, uid: str = Depends(require_account_uid)):
    if target_uid == uid:
        raise HTTPException(status_code=400, detail="Cannot swipe on yourself")
    if not users_service.get_profile(uid):
        # require_account_uid already confirmed the caller is a person or a
        # community; no person profile means this is a community swiping as
        # itself, which only unlocks once verified — mirrors the gate on
        # publishing posts (anti-spam: an unreviewed community shouldn't be
        # able to like people).
        community = communities_service.get_community(uid)
        if not community or community.get("verification") != "verified":
            raise HTTPException(status_code=403, detail="Community must be verified to like people")
    try:
        match_id = matching_service.record_swipe(uid, target_uid, payload.action)
    except BlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"matched": match_id is not None, "matchId": match_id}


@router.delete("/{target_uid}")
def undo_swipe(target_uid: str, uid: str = Depends(require_account_uid)):
    # Rewind: forget the caller's last swipe on target_uid so they reappear
    # in the feed. Refused once the pair has an active match.
    try:
        matching_service.undo_swipe(uid, target_uid)
    except MatchedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}
