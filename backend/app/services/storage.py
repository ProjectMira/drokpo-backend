import uuid
from urllib.parse import quote

from app.firebase import get_bucket

# Firebase Storage serves blobs with `private, max-age=0` unless the object
# carries its own Cache-Control metadata — meaning every deck re-entry
# re-downloads the same photo. The app writes each upload to a fresh
# storagePath (never a different image over the same path), so a long
# device/CDN cache is safe.
PHOTO_CACHE_CONTROL = "public, max-age=2592000"


def photo_path_prefix(uid: str) -> str:
    return f"users/{uid}/photos/"


def delete_blob(storage_path: str) -> None:
    blob = get_bucket().blob(storage_path)
    if blob.exists():
        blob.delete()


def ensure_download_url(storage_path: str) -> str | None:
    """Stable token-authenticated download URL for a blob, or None if missing.

    This is the same URL the Firebase client SDK's getDownloadURL() resolves —
    but the SDK pays a metadata round-trip per photo per render. Minting the
    token once server-side and storing the URL on the photo document lets the
    app load images directly, with zero preflight requests.

    Also stamps Cache-Control metadata on the blob so devices and Google's
    edge cache keep the bytes.
    """
    bucket = get_bucket()
    blob = bucket.get_blob(storage_path)
    if blob is None:
        return None

    metadata = dict(blob.metadata or {})
    token = metadata.get("firebaseStorageDownloadTokens")
    needs_patch = False
    if not token:
        token = str(uuid.uuid4())
        blob.metadata = {**metadata, "firebaseStorageDownloadTokens": token}
        needs_patch = True
    if blob.cache_control != PHOTO_CACHE_CONTROL:
        blob.cache_control = PHOTO_CACHE_CONTROL
        needs_patch = True
    if needs_patch:
        blob.patch()

    # The SDK can accumulate several comma-separated tokens; any one works.
    token = token.split(",")[0]
    return (
        f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/"
        f"{quote(storage_path, safe='')}?alt=media&token={token}"
    )
