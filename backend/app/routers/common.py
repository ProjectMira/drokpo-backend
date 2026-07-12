from fastapi import HTTPException

from app.models.user import PhotoConfirm
from app.services import storage as storage_service
from app.services import users as users_service


def require_owned_photo_path(uid: str, storage_path: str, verb: str) -> None:
    if not storage_path.startswith(storage_service.photo_path_prefix(uid)):
        raise HTTPException(status_code=403, detail=f"Cannot {verb} a photo outside your own path")


def attach_photo(uid: str, payload: PhotoConfirm) -> None:
    require_owned_photo_path(uid, payload.storagePath, "attach")
    # Resolving the URL doubles as the existence check (None = no such blob),
    # and stores a ready-to-render link so the app never has to call
    # getDownloadURL() per photo.
    url = storage_service.ensure_download_url(payload.storagePath)
    if url is None:
        raise HTTPException(status_code=400, detail="Photo not found in storage; upload it first")
    try:
        users_service.add_photo(uid, payload.storagePath, payload.order, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
