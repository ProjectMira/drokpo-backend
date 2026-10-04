import uuid
from urllib.parse import quote, unquote, urlparse

from google.api_core import exceptions as google_exceptions

from app.firebase import get_bucket

# Firebase Storage serves blobs with `private, max-age=0` unless the object
# carries its own Cache-Control metadata — meaning every deck re-entry
# re-downloads the same photo. The app writes each upload to a fresh
# storagePath (never a different image over the same path), so a long
# device/CDN cache is safe.
PHOTO_CACHE_CONTROL = "public, max-age=2592000"


def photo_path_prefix(uid: str) -> str:
    return f"users/{uid}/photos/"


def community_photo_path_prefix(uid: str) -> str:
    return f"communities/{uid}/photos/"


def delete_blob(storage_path: str) -> None:
    blob = get_bucket().blob(storage_path)
    if blob.exists():
        blob.delete()


def delete_prefix(prefix: str, keep: set[str] | frozenset[str] = frozenset()) -> None:
    """Delete every blob under a folder prefix except the paths in `keep`.

    Account deletion sweeps whole per-uid folders this way, so files no doc
    references any more (a photo removed from the profile, an abandoned chat
    upload) go too. The prefix must end in "/": "users/abc" would also match
    another account's "users/abcd/...".
    """
    if not prefix.endswith("/"):
        raise ValueError("prefix must end with '/'")
    for blob in get_bucket().list_blobs(prefix=prefix):
        if blob.name in keep:
            continue
        try:
            blob.delete()
        except google_exceptions.NotFound:
            pass  # already gone — the goal state holds


def path_from_download_url(url: str | None) -> str | None:
    """The storagePath a Firebase token download URL points at — the inverse
    of ensure_download_url, and the same URL shape the client SDK's
    getDownloadURL() returns. None for any other URL."""
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.netloc != "firebasestorage.googleapis.com" or "/o/" not in parsed.path:
        return None
    return unquote(parsed.path.split("/o/", 1)[1])


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
