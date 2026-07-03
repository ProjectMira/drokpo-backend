from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_uid
from app.models.swipe import SwipeIn
from app.services import matching as matching_service
from app.services.matching import BlockedError

router = APIRouter(prefix="/swipes", tags=["swipes"])


@router.post("/{target_uid}")
def swipe(target_uid: str, payload: SwipeIn, uid: str = Depends(get_current_uid)):
    if target_uid == uid:
        raise HTTPException(status_code=400, detail="Cannot swipe on yourself")
    try:
        match_id = matching_service.record_swipe(uid, target_uid, payload.action)
    except BlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"matched": match_id is not None, "matchId": match_id}
