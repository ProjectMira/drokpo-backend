import time
from datetime import datetime, timezone

from firebase_admin import firestore

from app.firebase import get_firestore
from app.models.community_post import CommunityPostIn, CommunityPostUpdate

COMMUNITY_POSTS = "communityPosts"
VOTES = "votes"
RSVPS = "rsvps"
# Firestore "in" filters accept at most 30 values per query.
FIRESTORE_IN_CHUNK_SIZE = 30

# Fields exposed to clients — never impressions/clicks (server-side analytics
# only, same convention as ads/news). `active` is harmless to expose (it's
# already implied by which posts a non-owner's query can even return) and lets
# a community's own post-management list show unpublished posts as such.
PUBLIC_POST_FIELDS = (
    "communityId",
    "communityName",
    "communityLogoUrl",
    "kind",
    "title",
    "body",
    "imageUrl",
    "linkUrl",
    "ctaLabel",
    "poll",
    "eventAt",
    "location",
    "attendeeCount",
    "active",
    "createdAt",
)


class NotFoundError(Exception):
    """The community a post is being created for doesn't exist at all — the
    caller's uid isn't a community account."""


class NotVerifiedError(Exception):
    pass


class PostNotFoundError(Exception):
    pass


class NotAPollError(Exception):
    pass


class InvalidOptionError(Exception):
    pass


class NotAnEventError(Exception):
    pass


def _option_docs(labels: list[str]) -> list[dict]:
    return [{"id": f"opt{i + 1}", "label": label} for i, label in enumerate(labels)]


def create_post(cid: str, payload: CommunityPostIn) -> str:
    from app.services import communities as communities_service

    payload.validate_kind_shape()
    community = communities_service.get_community(cid)
    if not community:
        raise NotFoundError("Community not found")
    if community.get("verification") != "verified":
        raise NotVerifiedError("Posting unlocks once your community is verified")

    image_url = payload.imageUrl
    if not image_url and payload.photoStoragePath:
        from app.services import storage as storage_service

        if not payload.photoStoragePath.startswith(storage_service.community_photo_path_prefix(cid)):
            raise ValueError("photoStoragePath must be one of your own community photos")
        image_url = storage_service.ensure_download_url(payload.photoStoragePath)

    photos = community.get("photos") or []
    logo_url = photos[0].get("url") if photos else None

    db = get_firestore()
    doc = {
        "communityId": cid,
        "communityName": community.get("name"),
        "communityLogoUrl": logo_url,
        "kind": payload.kind,
        "title": payload.title,
        "body": payload.body,
        "imageUrl": image_url,
        "linkUrl": payload.linkUrl,
        "ctaLabel": payload.ctaLabel,
        "poll": None,
        "eventAt": None,
        "location": None,
        "attendeeCount": None,
        "active": True,
        "impressions": 0,
        "clicks": 0,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "updatedAt": firestore.SERVER_TIMESTAMP,
    }
    if payload.kind == "poll":
        options = _option_docs([o.strip() for o in payload.pollOptions if o.strip()])
        doc["poll"] = {"options": options, "counts": {opt["id"]: 0 for opt in options}}
    if payload.kind == "event":
        doc["eventAt"] = payload.eventAt
        doc["location"] = payload.location
        doc["attendeeCount"] = 0

    _, ref = db.collection(COMMUNITY_POSTS).add(doc)
    return ref.id


def update_post(cid: str, post_id: str, payload: CommunityPostUpdate) -> None:
    db = get_firestore()
    ref = db.collection(COMMUNITY_POSTS).document(post_id)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("communityId") != cid:
        raise PostNotFoundError("Post not found")
    # kind/pollOptions aren't updatable fields on CommunityPostUpdate at all —
    # poll options are immutable once created because votes reference their ids.
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        return
    updates["updatedAt"] = firestore.SERVER_TIMESTAMP
    ref.update(updates)


def _hydrate_my_votes(db, uid: str, posts: list[dict]) -> dict[str, str]:
    poll_post_ids = [p["postId"] for p in posts if p.get("kind") == "poll"]
    if not poll_post_ids:
        return {}
    refs = [db.collection(COMMUNITY_POSTS).document(pid).collection(VOTES).document(uid) for pid in poll_post_ids]
    my_votes: dict[str, str] = {}
    for snap in db.get_all(refs):
        if snap.exists:
            # get_all doesn't preserve ref order; recover the post id from the
            # vote doc's own path instead of zipping against poll_post_ids.
            post_id = snap.reference.parent.parent.id
            my_votes[post_id] = snap.to_dict().get("optionId")
    return my_votes


def _hydrate_my_rsvps(db, uid: str, posts: list[dict]) -> set[str]:
    event_post_ids = [p["postId"] for p in posts if p.get("kind") == "event"]
    if not event_post_ids:
        return set()
    refs = [db.collection(COMMUNITY_POSTS).document(pid).collection(RSVPS).document(uid) for pid in event_post_ids]
    going: set[str] = set()
    for snap in db.get_all(refs):
        if snap.exists:
            going.add(snap.reference.parent.parent.id)
    return going


def _to_public_posts(db, uid: str, posts: list[dict]) -> list[dict]:
    """Shape raw post docs into the client-facing view, attaching myVote/
    myRsvp for the calling uid. Shared by list_posts and list_feed_for_member.
    (list_active_for_feed intentionally skips this — it's a 60s cache shared
    across every viewer, so it can't carry any one caller's vote/RSVP state.)
    """
    my_votes = _hydrate_my_votes(db, uid, posts)
    my_rsvps = _hydrate_my_rsvps(db, uid, posts)
    result = []
    for post in posts:
        public = {"postId": post["postId"], **{k: post[k] for k in PUBLIC_POST_FIELDS if k in post}}
        if post.get("kind") == "poll":
            public["myVote"] = my_votes.get(post["postId"])
        if post.get("kind") == "event":
            public["myRsvp"] = post["postId"] in my_rsvps
        result.append(public)
    return result


def list_posts(cid: str, uid: str, limit: int = 20, before: str | None = None) -> list[dict]:
    """A community's posts, newest first. The community itself (uid == cid)
    sees its own unpublished (active == False) posts too, so its post-manager
    screen can show and let it re-publish them; everyone else only ever sees
    active posts, matching what the Discover feed shows."""
    db = get_firestore()
    query = db.collection(COMMUNITY_POSTS).where("communityId", "==", cid)
    if uid != cid:
        query = query.where("active", "==", True)
    query = query.order_by("createdAt", direction=firestore.Query.DESCENDING)
    if before:
        cursor = db.collection(COMMUNITY_POSTS).document(before).get()
        if cursor.exists:
            query = query.start_after(cursor)
    posts = [{"postId": doc.id, **doc.to_dict()} for doc in query.limit(limit).stream()]
    return _to_public_posts(db, uid, posts)


def list_feed_for_member(uid: str, limit: int = 30) -> list[dict]:
    """Posts from every community `uid` has joined, newest first — the
    person-side Communities tab's "from your communities" section."""
    from app.services import communities as communities_service

    community_ids = communities_service.get_joined_community_ids(uid)
    if not community_ids:
        return []

    db = get_firestore()
    posts: list[dict] = []
    for i in range(0, len(community_ids), FIRESTORE_IN_CHUNK_SIZE):
        chunk = community_ids[i : i + FIRESTORE_IN_CHUNK_SIZE]
        query = (
            db.collection(COMMUNITY_POSTS)
            .where("communityId", "in", chunk)
            .where("active", "==", True)
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        posts.extend({"postId": doc.id, **doc.to_dict()} for doc in query.stream())

    posts.sort(key=lambda p: p["createdAt"], reverse=True)
    return _to_public_posts(db, uid, posts[:limit])


@firestore.transactional
def _vote_transaction(transaction, db, post_id: str, uid: str, option_id: str) -> dict:
    post_ref = db.collection(COMMUNITY_POSTS).document(post_id)
    vote_ref = post_ref.collection(VOTES).document(uid)

    # Both reads before any write — Firestore transactions require it.
    post_snap = post_ref.get(transaction=transaction)
    vote_snap = vote_ref.get(transaction=transaction)

    if not post_snap.exists or not post_snap.to_dict().get("active"):
        raise PostNotFoundError("Post not found")
    post = post_snap.to_dict()
    poll = post.get("poll")
    if post.get("kind") != "poll" or not poll:
        raise NotAPollError("This post is not a poll")
    valid_ids = {opt["id"] for opt in poll["options"]}
    if option_id not in valid_ids:
        raise InvalidOptionError("Unknown poll option")

    previous_option = vote_snap.to_dict().get("optionId") if vote_snap.exists else None
    if previous_option == option_id:
        return poll  # re-voting for your current option changes nothing

    counts = dict(poll.get("counts", {}))
    if previous_option:
        counts[previous_option] = max(0, counts.get(previous_option, 0) - 1)
    counts[option_id] = counts.get(option_id, 0) + 1

    transaction.set(
        vote_ref,
        {
            "optionId": option_id,
            "createdAt": vote_snap.get("createdAt") if vote_snap.exists else firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        },
    )
    transaction.update(post_ref, {"poll.counts": counts})
    return {"options": poll["options"], "counts": counts}


def vote(post_id: str, uid: str, option_id: str) -> dict:
    db = get_firestore()
    transaction = db.transaction()
    poll = _vote_transaction(transaction, db, post_id, uid, option_id)
    return {"poll": poll, "myVote": option_id}


@firestore.transactional
def _rsvp_transaction(transaction, db, post_id: str, uid: str, going: bool) -> int:
    post_ref = db.collection(COMMUNITY_POSTS).document(post_id)
    rsvp_ref = post_ref.collection(RSVPS).document(uid)

    # Both reads before any write — Firestore transactions require it.
    post_snap = post_ref.get(transaction=transaction)
    rsvp_snap = rsvp_ref.get(transaction=transaction)

    if not post_snap.exists or not post_snap.to_dict().get("active"):
        raise PostNotFoundError("Post not found")
    post = post_snap.to_dict()
    if post.get("kind") != "event":
        raise NotAnEventError("This post is not an event")

    already_going = rsvp_snap.exists
    count = post.get("attendeeCount") or 0
    if going == already_going:
        return count  # no-op: RSVPing when already going, or un-RSVPing when not

    if going:
        transaction.set(rsvp_ref, {"createdAt": firestore.SERVER_TIMESTAMP})
        count += 1
    else:
        transaction.delete(rsvp_ref)
        count = max(0, count - 1)
    transaction.update(post_ref, {"attendeeCount": count})
    return count


def rsvp(post_id: str, uid: str, going: bool) -> dict:
    db = get_firestore()
    transaction = db.transaction()
    count = _rsvp_transaction(transaction, db, post_id, uid, going)
    return {"attendeeCount": count, "going": going}


def record_event(post_id: str, event: str) -> None:
    if event not in ("impression", "click"):
        raise ValueError("event must be 'impression' or 'click'")
    field = "impressions" if event == "impression" else "clicks"
    from google.api_core import exceptions as google_exceptions

    try:
        get_firestore().collection(COMMUNITY_POSTS).document(post_id).update({field: firestore.Increment(1)})
    except google_exceptions.NotFound as exc:
        raise PostNotFoundError("Post not found") from exc


# ---------------------------------------------------------------------------
# Discover feed integration (phase B3) — active posts from verified
# communities only, cached briefly like ads/news since they change on a human
# timescale (a community publishing a post), not per-request.
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 60
_feed_cache: dict = {"expires": 0.0, "limit": None, "posts": None}


def _is_past_event(post: dict) -> bool:
    if post.get("kind") != "event" or not post.get("eventAt"):
        return False
    try:
        event_at = datetime.fromisoformat(post["eventAt"])
    except ValueError:
        return False
    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)
    return event_at <= datetime.now(timezone.utc)


def list_active_for_feed(limit: int = 10) -> list[dict]:
    from app.services import communities as communities_service

    if (
        _feed_cache["posts"] is not None
        and _feed_cache["limit"] == limit
        and time.monotonic() < _feed_cache["expires"]
    ):
        return _feed_cache["posts"]

    db = get_firestore()
    query = (
        db.collection(COMMUNITY_POSTS)
        .where("active", "==", True)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit * 3)  # overfetch, then drop posts from not-yet-verified communities/past events
    )
    posts = [{"postId": doc.id, **doc.to_dict()} for doc in query.stream()]

    community_ids = [p.get("communityId") for p in posts if p.get("communityId")]
    verified = communities_service.get_verified_uids(community_ids)

    result = [
        {"postId": p["postId"], **{k: p[k] for k in PUBLIC_POST_FIELDS if k in p}}
        for p in posts
        if p.get("communityId") in verified and not _is_past_event(p)
    ][:limit]
    _feed_cache.update(expires=time.monotonic() + _CACHE_TTL_SECONDS, limit=limit, posts=result)
    return result


def attach_viewer_state(uid: str, posts: list[dict]) -> list[dict]:
    """Overlay the calling viewer's myVote/myRsvp onto viewer-agnostic post
    dicts (the shared feed cache can't carry them — see _to_public_posts).
    Returns copies; the cached dicts themselves must never gain per-user
    fields or one viewer's state would leak to everyone for 60 seconds."""
    if not any(p.get("kind") in ("poll", "event") for p in posts):
        return posts  # nothing viewer-specific to attach; skip Firestore
    db = get_firestore()
    my_votes = _hydrate_my_votes(db, uid, posts)
    my_rsvps = _hydrate_my_rsvps(db, uid, posts)
    result = []
    for post in posts:
        out = dict(post)
        if out.get("kind") == "poll":
            out["myVote"] = my_votes.get(out["postId"])
        if out.get("kind") == "event":
            out["myRsvp"] = out["postId"] in my_rsvps
        result.append(out)
    return result
