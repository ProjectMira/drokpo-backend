from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_account_uid
from app.services import matches as matches_service

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("")
def list_matches(uid: str = Depends(require_account_uid)):
    return {"matches": matches_service.list_for_user(uid)}


@router.post("/{match_id}/unmatch")
def unmatch(match_id: str, uid: str = Depends(require_account_uid)):
    try:
        matches_service.unmatch(match_id, uid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
