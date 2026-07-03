from fastapi import HTTPException

from app.models.user import PhotoConfirm
from app.services import storage as storage_service
from app.services import users as users_service


def require_owned_photo_path(uid: str, storage_path: str, verb: str) -> None:
    if not storage_path.startswith(storage_service.photo_path_prefix(uid)):
        raise HTTPException(status_code=403, detail=f"Cannot {verb} a photo outside your own path")


def attach_photo(uid: str, payload: PhotoConfirm) -> None:
    require_owned_photo_path(uid, payload.storagePath, "attach")
    if not storage_service.blob_exists(payload.storagePath):
        raise HTTPException(status_code=400, detail="Photo not found in storage; upload it first")
    try:
        users_service.add_photo(uid, payload.storagePath, payload.order)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
