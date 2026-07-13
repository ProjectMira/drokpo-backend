from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_uid
from app.models.user import OnboardingIn, PhotoConfirm
from app.routers.common import attach_photo
from app.services import communities as communities_service
from app.services import users as users_service

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("")
def create_profile(payload: OnboardingIn, uid: str = Depends(get_current_uid)):
    if communities_service.community_exists(uid):
        raise HTTPException(status_code=409, detail="This account is already registered as a community")
    if users_service.get_profile(uid):
        raise HTTPException(status_code=409, detail="Profile already exists")
    users_service.create_profile(uid, payload)
    return {"uid": uid}


@router.post("/photos/confirm")
def confirm_photo(payload: PhotoConfirm, uid: str = Depends(get_current_uid)):
    attach_photo(uid, payload)
    return {"ok": True}


@router.post("/complete")
def complete_onboarding(uid: str = Depends(get_current_uid)):
    try:
        users_service.complete_onboarding(uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
