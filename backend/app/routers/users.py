from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import require_account_uid
from app.firebase import get_firestore
from app.services import counterparts as counterparts_service
from app.services import matching as matching_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{target_uid}")
def get_user_card(target_uid: str, uid: str = Depends(require_account_uid)):
    """Public card for one person or community account — resolves shared
    profile links (drokpo.../s/user/{uid}). A pair in a block relationship
    gets a 404, indistinguishable from a deleted account."""
    if target_uid != uid and matching_service._either_blocked(get_firestore(), uid, target_uid):
        raise HTTPException(status_code=404, detail="User not found")
    card = counterparts_service.get_public_counterparts([target_uid]).get(target_uid)
    if not card:
        raise HTTPException(status_code=404, detail="User not found")
    return card
