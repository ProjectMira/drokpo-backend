"""Unit tests for the download-URL minting in the storage service, using a
stub bucket — the token/cache-control handling is the part with real logic."""

from urllib.parse import quote

from app.services import storage as storage_service
from app.services.storage import PHOTO_CACHE_CONTROL


class StubBlob:
    def __init__(self, metadata=None, cache_control=None):
        self.metadata = metadata
        self.cache_control = cache_control
        self.patched = False

    def patch(self):
        self.patched = True


class StubBucket:
    name = "drokpo-test.firebasestorage.app"

    def __init__(self, blob):
        self._blob = blob

    def get_blob(self, path):
        return self._blob


def test_mints_token_and_cache_control_for_new_blob(monkeypatch):
    blob = StubBlob()
    monkeypatch.setattr(storage_service, "get_bucket", lambda: StubBucket(blob))

    url = storage_service.ensure_download_url("users/u1/photos/a.jpg")

    token = blob.metadata["firebaseStorageDownloadTokens"]
    assert token  # a fresh token was minted…
    assert blob.cache_control == PHOTO_CACHE_CONTROL  # …with cache metadata…
    assert blob.patched  # …persisted to Storage
    path = quote("users/u1/photos/a.jpg", safe="")
    assert url == (
        f"https://firebasestorage.googleapis.com/v0/b/{StubBucket.name}/o/"
        f"{path}?alt=media&token={token}"
    )


def test_reuses_existing_token_without_patch(monkeypatch):
    blob = StubBlob(
        metadata={"firebaseStorageDownloadTokens": "tok-1"},
        cache_control=PHOTO_CACHE_CONTROL,
    )
    monkeypatch.setattr(storage_service, "get_bucket", lambda: StubBucket(blob))

    url = storage_service.ensure_download_url("users/u1/photos/a.jpg")

    assert url.endswith("token=tok-1")
    assert not blob.patched  # nothing changed, so no metadata write


def test_uses_first_of_multiple_tokens(monkeypatch):
    # The client SDK can accumulate comma-separated tokens; any single one works.
    blob = StubBlob(
        metadata={"firebaseStorageDownloadTokens": "tok-1,tok-2"},
        cache_control=PHOTO_CACHE_CONTROL,
    )
    monkeypatch.setattr(storage_service, "get_bucket", lambda: StubBucket(blob))
    assert storage_service.ensure_download_url("users/u1/photos/a.jpg").endswith("token=tok-1")


def test_missing_blob_returns_none(monkeypatch):
    class EmptyBucket(StubBucket):
        def get_blob(self, path):
            return None

    monkeypatch.setattr(storage_service, "get_bucket", lambda: EmptyBucket(None))
    assert storage_service.ensure_download_url("users/u1/photos/missing.jpg") is None
