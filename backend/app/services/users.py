from datetime import date

from firebase_admin import auth as firebase_auth
from firebase_admin import firestore

from app.firebase import ensure_app, get_firestore
from app.models.user import OnboardingIn, ProfileUpdate
from app.services import geo

USERS = "users"
MAX_PHOTOS = 6

# Fields safe to show to other members (no location, preferences, or fcmTokens).
PUBLIC_FIELDS = (
    "displayName",
    "dob",
    "gender",
    "bio",
    "occupation",
    "education",
    "region",
    "languages",
    "interests",
    "socials",
    "photos",
)


def public_summary(uid: str, data: dict) -> dict:
    return {"uid": uid, **{k: data[k] for k in PUBLIC_FIELDS if k in data}}


def get_public_profiles(uids: list[str]) -> dict[str, dict]:
    """Batch-read the public view of several profiles, keyed by uid."""
    unique = list(dict.fromkeys(uids))
    if not unique:
        return {}
    db = get_firestore()
    refs = [db.collection(USERS).document(uid) for uid in unique]
    return {snap.id: public_summary(snap.id, snap.to_dict()) for snap in db.get_all(refs) if snap.exists}


def create_profile(uid: str, payload: OnboardingIn) -> None:
    db = get_firestore()
    data = payload.model_dump()
    location = data.pop("location")
    location["geohash"] = geo.encode(location["lat"], location["lng"])
    data["location"] = location
    data.update(
        {
            "photos": [],
            "fcmTokens": [],
            "status": "active",
            "onboardingComplete": False,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
    )
    db.collection(USERS).document(uid).set(data, merge=True)


def get_profile(uid: str) -> dict | None:
    snap = get_firestore().collection(USERS).document(uid).get()
    return {"uid": uid, **snap.to_dict()} if snap.exists else None


def update_profile(uid: str, payload: ProfileUpdate) -> None:
    data = payload.model_dump(exclude_unset=True)
    # Flatten preferences/socials into dotted field paths so a partial object
    # merges into the stored map instead of replacing it wholesale (which
    # would reset omitted subfields to their defaults).
    prefs = data.pop("preferences", None) or {}
    socials = data.pop("socials", None) or {}
    location = data.pop("location", None)
    updates = {k: v for k, v in data.items() if v is not None}
    updates.update({f"preferences.{k}": v for k, v in prefs.items() if v is not None})
    updates.update({f"socials.{k}": v for k, v in socials.items() if v is not None})
    if location is not None:
        # Location is replaced as a whole; the geohash is always derived
        # server-side so a client can't desync it from lat/lng.
        location["geohash"] = geo.encode(location["lat"], location["lng"])
        updates["location"] = location
    if not updates:
        return
    updates["updatedAt"] = firestore.SERVER_TIMESTAMP
    get_firestore().collection(USERS).document(uid).update(updates)


def add_photo(uid: str, storage_path: str, order: int) -> None:
    ref = get_firestore().collection(USERS).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []
    if len(photos) >= MAX_PHOTOS:
        raise ValueError(f"Maximum of {MAX_PHOTOS} photos allowed")
    ref.update(
        {
            "photos": firestore.ArrayUnion([{"storagePath": storage_path, "order": order}]),
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
    )


def reorder_photos(uid: str, storage_paths: list[str]) -> None:
    """Rewrite the photos array in the given order.

    The incoming paths must be exactly the same set the user already has (same
    multiset) — this only reorders, it can't add or drop photos. Per-photo keys
    (e.g. an existing url) are preserved; each photo's `order` is set to its new
    index and `photos[0]` becomes the primary/card photo.
    """
    ref = get_firestore().collection(USERS).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []

    by_path = {p.get("storagePath"): p for p in photos}
    if sorted(storage_paths) != sorted(by_path.keys()):
        raise ValueError("storagePaths must match your existing photos exactly")

    reordered = [{**by_path[path], "storagePath": path, "order": i} for i, path in enumerate(storage_paths)]
    ref.update({"photos": reordered, "updatedAt": firestore.SERVER_TIMESTAMP})


def remove_photo(uid: str, storage_path: str) -> None:
    ref = get_firestore().collection(USERS).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []
    remaining = [p for p in photos if p.get("storagePath") != storage_path]
    ref.update({"photos": remaining, "updatedAt": firestore.SERVER_TIMESTAMP})


def add_fcm_token(uid: str, token: str) -> None:
    get_firestore().collection(USERS).document(uid).update(
        {"fcmTokens": firestore.ArrayUnion([token]), "updatedAt": firestore.SERVER_TIMESTAMP}
    )


def remove_fcm_token(uid: str, token: str) -> None:
    get_firestore().collection(USERS).document(uid).update(
        {"fcmTokens": firestore.ArrayRemove([token]), "updatedAt": firestore.SERVER_TIMESTAMP}
    )


def complete_onboarding(uid: str) -> None:
    profile = get_profile(uid)
    if not profile or not profile.get("photos"):
        raise ValueError("At least one photo is required to complete onboarding")
    get_firestore().collection(USERS).document(uid).update(
        {"onboardingComplete": True, "updatedAt": firestore.SERVER_TIMESTAMP}
    )


def delete_account(uid: str) -> None:
    """Remove the user's data and Firebase Auth account.

    Matches are flipped to "unmatched" (not deleted) so the other participant's
    chat history — and any evidence attached to reports — survives, same as a
    normal unmatch.
    """
    from app.services import storage as storage_service

    db = get_firestore()
    ref = db.collection(USERS).document(uid)
    snap = ref.get()
    profile = snap.to_dict() if snap.exists else {}

    for photo in profile.get("photos", []):
        path = photo.get("storagePath")
        if path:
            storage_service.delete_blob(path)

    for match in db.collection("matches").where("users", "array_contains", uid).stream():
        if match.to_dict().get("status") == "active":
            match.reference.update({"status": "unmatched"})

    for swipe in ref.collection("swipes").stream():
        swipe.reference.delete()
    for blocked in db.collection("blocks").document(uid).collection("blockedUsers").stream():
        blocked.reference.delete()

    ref.delete()

    ensure_app()
    try:
        firebase_auth.delete_user(uid)
    except firebase_auth.UserNotFoundError:
        pass


def _within_age(dob: str | None, age_min: int | None, age_max: int | None) -> bool:
    if age_min is None and age_max is None:
        return True
    if not dob:
        return False
    try:
        born = date.fromisoformat(dob)
    except ValueError:
        return False
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return (age_min or 0) <= age <= (age_max if age_max is not None else 200)


def _blocked_uids(db, uid: str) -> set[str]:
    block_doc = db.collection("blocks").document(uid)
    blocked = {d.id for d in block_doc.collection("blockedUsers").stream()}
    blocked |= {d.id for d in block_doc.collection("blockedBy").stream()}
    return blocked


def get_candidates(uid: str, profile: dict, limit: int = 20) -> list[dict]:
    db = get_firestore()
    prefs = profile.get("preferences", {})
    geohash = profile.get("location", {}).get("geohash", "")
    prefix = geo.feed_prefix(geohash)

    # U+F8FF sorts after every character used in geohashes, so the range
    # [prefix, prefix + "\uf8ff") covers exactly the strings starting with prefix.
    query = (
        db.collection(USERS)
        .where("status", "==", "active")
        .where("location.geohash", ">=", prefix)
        .where("location.geohash", "<", prefix + "\uf8ff")
        .limit(limit * 3)  # overfetch since we filter swiped/gender/age/blocked below
    )
    candidate_docs = [d for d in query.stream() if d.id != uid]
    if not candidate_docs:
        return []

    # Batch-read only the swipe docs for this candidate page, instead of
    # streaming the caller's entire (unbounded) swipe history.
    swipes = db.collection(USERS).document(uid).collection("swipes")
    swipe_refs = [swipes.document(d.id) for d in candidate_docs]
    swiped_ids = {s.id for s in db.get_all(swipe_refs) if s.exists}

    excluded = _blocked_uids(db, uid)
    my_interests = set(profile.get("interests", []))
    age_min, age_max = prefs.get("ageMin"), prefs.get("ageMax")

    # Rank the whole overfetched page by shared interests (most in common
    # first) rather than returning in geohash order, so the feed surfaces
    # the most compatible potential friends nearby.
    ranked = []
    for doc in candidate_docs:
        if doc.id in swiped_ids or doc.id in excluded:
            continue
        data = doc.to_dict()
        if not _within_age(data.get("dob"), age_min, age_max):
            continue
        shared = len(my_interests & set(data.get("interests", [])))
        ranked.append((shared, {"uid": doc.id, **data}))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [candidate for _, candidate in ranked[:limit]]
