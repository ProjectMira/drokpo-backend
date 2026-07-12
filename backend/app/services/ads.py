import time

from firebase_admin import firestore
from google.api_core import exceptions as google_exceptions

from app.firebase import get_firestore

ADS = "ads"

# Fields exposed to the client. Ads are authored by hand in the Firebase
# console (see docs/ADS.md); everything else on the doc (active, order,
# impressions, clicks, notes…) stays server-side.
PUBLIC_AD_FIELDS = ("title", "body", "linkUrl", "ctaLabel", "imageUrl", "photos")

VALID_EVENTS = ("impression", "click")

# Ads ride along with *every* feed page but change on a human timescale
# (edited by hand in the console), so a short in-process cache removes a
# Firestore query per feed request. 60s keeps console edits near-instant.
_CACHE_TTL_SECONDS = 60
_cache: dict = {"expires": 0.0, "limit": None, "ads": None}


def _resolve_photo_urls(photos: list) -> list[dict]:
    """Fill in a download url for storagePath-only ad photos so the app can
    render them directly instead of a per-photo Storage SDK round-trip."""
    from app.services import storage as storage_service

    resolved = []
    for photo in photos or []:
        if isinstance(photo, dict) and photo.get("storagePath") and not photo.get("url"):
            try:
                url = storage_service.ensure_download_url(photo["storagePath"])
            except Exception:
                url = None  # a broken creative must not break the feed
            if url:
                photo = {**photo, "url": url}
        resolved.append(photo)
    return resolved


def list_active(limit: int = 20) -> list[dict]:
    """Active ads, lowest `order` first, for interleaving into the feed.

    Ads without a linkUrl are skipped — the whole point of an ad card is
    that liking it opens the link.
    """
    if _cache["ads"] is not None and _cache["limit"] == limit and time.monotonic() < _cache["expires"]:
        return _cache["ads"]

    db = get_firestore()
    query = db.collection(ADS).where("active", "==", True).limit(limit)
    ads = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        if not data.get("linkUrl") or not data.get("title"):
            continue
        ad = {"adId": doc.id, **{k: data[k] for k in PUBLIC_AD_FIELDS if k in data}}
        if ad.get("photos"):
            ad["photos"] = _resolve_photo_urls(ad["photos"])
        ads.append((data.get("order", 0), ad))
    ads.sort(key=lambda pair: pair[0])
    result = [ad for _, ad in ads]
    _cache.update(expires=time.monotonic() + _CACHE_TTL_SECONDS, limit=limit, ads=result)
    return result


def record_event(ad_id: str, event: str) -> None:
    """Bump the impression/click counter on an ad doc."""
    if event not in VALID_EVENTS:
        raise ValueError(f"event must be one of {VALID_EVENTS}")
    field = "impressions" if event == "impression" else "clicks"
    try:
        get_firestore().collection(ADS).document(ad_id).update({field: firestore.Increment(1)})
    except google_exceptions.NotFound as exc:
        raise ValueError("Ad not found") from exc
