from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_uid, require_account_uid, require_person_uid
from app.models.user import FcmTokenIn, PhotoConfirm, PhotoOrderIn, ProfileUpdate
from app.routers.common import attach_photo, require_owned_photo_path
from app.services import communities as communities_service
from app.services import storage as storage_service
from app.services import users as users_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me")
def get_my_profile(uid: str = Depends(get_current_uid)):
    # Deliberately NOT require_person_uid: a 404 here (not 403) is the bootstrap
    # signal app versions shipped before GET /api/account existed rely on to
    # route into onboarding — see SessionStore's history and APIClient's
    # no-store comment. Breaking that would strand old clients on install.
    profile = users_service.get_profile(uid)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.delete("/me")
def delete_my_account(uid: str = Depends(require_person_uid)):
    users_service.delete_account(uid)
    return {"ok": True}


@router.patch("/me")
def update_my_profile(payload: ProfileUpdate, uid: str = Depends(require_person_uid)):
    users_service.update_profile(uid, payload)
    return {"ok": True}


@router.post("/me/photos")
def add_photo(payload: PhotoConfirm, uid: str = Depends(require_person_uid)):
    attach_photo(uid, payload)
    return {"ok": True}


@router.patch("/me/photos/order")
def reorder_photos(payload: PhotoOrderIn, uid: str = Depends(require_person_uid)):
    try:
        users_service.reorder_photos(uid, payload.storagePaths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/me/photos")
def delete_photo(storage_path: str, uid: str = Depends(require_person_uid)):
    require_owned_photo_path(uid, storage_path, "delete")
    users_service.remove_photo(uid, storage_path)
    storage_service.delete_blob(storage_path)
    return {"ok": True}


@router.post("/me/fcm-tokens")
def register_fcm_token(payload: FcmTokenIn, uid: str = Depends(require_account_uid)):
    # Communities now receive pushes too (likes/matches/messages as
    # themselves), so the token lands on whichever doc actually exists.
    if users_service.get_profile(uid):
        users_service.add_fcm_token(uid, payload.token)
    else:
        communities_service.add_fcm_token(uid, payload.token)
    return {"ok": True}


@router.delete("/me/fcm-tokens")
def remove_fcm_token(token: str, uid: str = Depends(require_account_uid)):
    if users_service.get_profile(uid):
        users_service.remove_fcm_token(uid, token)
    else:
        communities_service.remove_fcm_token(uid, token)
    return {"ok": True}
