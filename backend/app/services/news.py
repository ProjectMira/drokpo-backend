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


def record_event(news_id: str, event: str) -> None:
    if event not in VALID_EVENTS:
        raise ValueError(f"event must be one of {VALID_EVENTS}")
    field = "impressions" if event == "impression" else "clicks"
    try:
        get_firestore().collection(NEWS).document(news_id).update({field: firestore.Increment(1)})
    except google_exceptions.NotFound as exc:
        raise ValueError("News item not found") from exc
