from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_current_uid
from app.models.user import FcmTokenIn, PhotoConfirm, PhotoOrderIn, ProfileUpdate
from app.routers.common import attach_photo, require_owned_photo_path
from app.services import storage as storage_service
from app.services import users as users_service

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("/me")
def get_my_profile(uid: str = Depends(get_current_uid)):
    profile = users_service.get_profile(uid)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.delete("/me")
def delete_my_account(uid: str = Depends(get_current_uid)):
    users_service.delete_account(uid)
    return {"ok": True}


@router.patch("/me")
def update_my_profile(payload: ProfileUpdate, uid: str = Depends(get_current_uid)):
    users_service.update_profile(uid, payload)
    return {"ok": True}


@router.post("/me/photos")
def add_photo(payload: PhotoConfirm, uid: str = Depends(get_current_uid)):
    attach_photo(uid, payload)
    return {"ok": True}


@router.patch("/me/photos/order")
def reorder_photos(payload: PhotoOrderIn, uid: str = Depends(get_current_uid)):
    try:
        users_service.reorder_photos(uid, payload.storagePaths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/me/photos")
def delete_photo(storage_path: str, uid: str = Depends(get_current_uid)):
    require_owned_photo_path(uid, storage_path, "delete")
    users_service.remove_photo(uid, storage_path)
    storage_service.delete_blob(storage_path)
    return {"ok": True}


@router.post("/me/fcm-tokens")
def register_fcm_token(payload: FcmTokenIn, uid: str = Depends(get_current_uid)):
    users_service.add_fcm_token(uid, payload.token)
    return {"ok": True}


@router.delete("/me/fcm-tokens")
def remove_fcm_token(token: str, uid: str = Depends(get_current_uid)):
    users_service.remove_fcm_token(uid, token)
    return {"ok": True}
