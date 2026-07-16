import time

from firebase_admin import firestore
from google.api_core import exceptions as google_exceptions

from app.firebase import get_firestore

NEWS = "news"

# Fields exposed to the client. News docs are authored by the news-digest
# Claude skill (.claude/skills/news-digest/), which scrapes configured sites,
# writes an original gist/summary per article, and upserts them here —
# everything else on the doc (order, impressions, clicks) stays server-side.
PUBLIC_NEWS_FIELDS = ("title", "gist", "summary", "sourceUrl", "sourceName", "imageUrl", "publishedAt")

VALID_EVENTS = ("impression", "click")

# Same caching rationale as ads.py: news changes on a human/skill-run
# timescale, not per-request, so a short in-process cache removes a Firestore
# query per feed request.
_CACHE_TTL_SECONDS = 60
_cache: dict = {"expires": 0.0, "limit": None, "news": None}


def list_active(limit: int = 20) -> list[dict]:
    """Active news cards, newest first, for interleaving into the feed.

    `order` is written by the news-digest skill as the negative of the
    article's published-epoch, so the plain ascending sort used everywhere
    else in this codebase (lowest order first, same as ads) yields
    newest-article-first here too.
    """
    if _cache["news"] is not None and _cache["limit"] == limit and time.monotonic() < _cache["expires"]:
        return _cache["news"]

    db = get_firestore()
    query = db.collection(NEWS).where("active", "==", True).limit(limit)
    items = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        if not data.get("title") or not data.get("gist") or not data.get("sourceUrl"):
            continue
        card = {"newsId": doc.id, **{k: data[k] for k in PUBLIC_NEWS_FIELDS if k in data}}
        items.append((data.get("order", 0), card))
    items.sort(key=lambda pair: pair[0])
    result = [card for _, card in items]
    _cache.update(expires=time.monotonic() + _CACHE_TTL_SECONDS, limit=limit, news=result)
    return result


def get_card(news_id: str) -> dict:
    """One story by id — resolves shared news links. Served even after the
    story ages out of the feed (active=False): a link shared last week must
    outlive the feed rotation."""
    doc = get_firestore().collection(NEWS).document(news_id).get()
    if not doc.exists:
        raise ValueError("News item not found")
    data = doc.to_dict() or {}
    return {"newsId": news_id, **{k: data[k] for k in PUBLIC_NEWS_FIELDS if k in data}}


def record_event(news_id: str, event: str) -> None:
    if event not in VALID_EVENTS:
        raise ValueError(f"event must be one of {VALID_EVENTS}")
    field = "impressions" if event == "impression" else "clicks"
    try:
        get_firestore().collection(NEWS).document(news_id).update({field: firestore.Increment(1)})
    except google_exceptions.NotFound as exc:
        raise ValueError("News item not found") from exc


# ---------------------------------------------------------------------------
# Liked news — swiping right on a news card saves it to the member's Likes
# tab. The like stores a snapshot of the card (not a reference): news docs
# deactivate as they age out, and a story saved on day 1 must stay readable.
# ---------------------------------------------------------------------------

USERS = "users"
LIKED_NEWS = "likedNews"


def like(uid: str, news_id: str) -> dict:
    """Idempotently save a snapshot to users/{uid}/likedNews/{newsId}."""
    db = get_firestore()
    doc = db.collection(NEWS).document(news_id).get()
    if not doc.exists:
        raise ValueError("News item not found")
    data = doc.to_dict() or {}
    snapshot = {"newsId": news_id, **{k: data[k] for k in PUBLIC_NEWS_FIELDS if k in data}}
    like_ref = db.collection(USERS).document(uid).collection(LIKED_NEWS).document(news_id)
    if not like_ref.get().exists:
        like_ref.set({**snapshot, "likedAt": firestore.SERVER_TIMESTAMP})
        # Server-side analytics counter, same family as impressions/clicks.
        db.collection(NEWS).document(news_id).update({"likes": firestore.Increment(1)})
    return snapshot


def unlike(uid: str, news_id: str) -> None:
    """Remove a saved story; missing likes are a no-op (idempotent)."""
    db = get_firestore()
    like_ref = db.collection(USERS).document(uid).collection(LIKED_NEWS).document(news_id)
    if not like_ref.get().exists:
        return
    like_ref.delete()
    try:
        db.collection(NEWS).document(news_id).update({"likes": firestore.Increment(-1)})
    except google_exceptions.NotFound:
        pass  # the story itself may have been deleted since; the unlike still stands


def liked_ids(uid: str) -> set[str]:
    """Ids of every story the member has saved — the feed uses this to keep
    already-liked stories from cycling back into the Discover deck. select([])
    fetches document names only, so this costs no field reads."""
    db = get_firestore()
    query = db.collection(USERS).document(uid).collection(LIKED_NEWS).select([])
    return {doc.id for doc in query.stream()}


def list_liked(uid: str, limit: int = 100) -> list[dict]:
    """The member's saved stories, newest-first by like time."""
    db = get_firestore()
    query = (
        db.collection(USERS)
        .document(uid)
        .collection(LIKED_NEWS)
        .order_by("likedAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    items = []
    for doc in query.stream():
        data = doc.to_dict() or {}
        liked_at = data.pop("likedAt", None)
        items.append({**data, "likedAt": liked_at.isoformat() if hasattr(liked_at, "isoformat") else liked_at})
    return items
