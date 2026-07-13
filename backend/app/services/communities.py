from firebase_admin import auth as firebase_auth
from firebase_admin import firestore

from app.firebase import ensure_app, get_firestore
from app.models.community import CommunityOnboardingIn, CommunityUpdate

COMMUNITIES = "communities"
MEMBERS = "members"  # communities/{cid}/members/{uid} — the community's side
MEMBERSHIPS = "memberships"  # users/{uid}/memberships/{cid} — the member's side
MAX_PHOTOS = 6


class NotFoundError(Exception):
    pass

# Fields shown on a verified community's public card (directory, post
# attribution). No contact info, no verification/memberCount internals beyond
# what's meant to be public.
PUBLIC_FIELDS = (
    "name",
    "description",
    "website",
    "socials",
    "photos",
    "verification",
    "memberCount",
)


def community_exists(uid: str) -> bool:
    return get_firestore().collection(COMMUNITIES).document(uid).get().exists


def create_community(uid: str, payload: CommunityOnboardingIn) -> None:
    db = get_firestore()
    data = payload.model_dump(exclude_none=True)
    data.setdefault("socials", {})
    data.update(
        {
            "photos": [],
            "verification": "pending",
            "memberCount": 0,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
    )
    db.collection(COMMUNITIES).document(uid).set(data, merge=True)


def get_community(uid: str) -> dict | None:
    snap = get_firestore().collection(COMMUNITIES).document(uid).get()
    return {"uid": uid, **snap.to_dict()} if snap.exists else None


def public_summary(uid: str, data: dict) -> dict:
    return {"uid": uid, **{k: data[k] for k in PUBLIC_FIELDS if k in data}}


def get_public_communities(uids: list[str]) -> dict[str, dict]:
    """Batch-read the public view of several communities, keyed by uid."""
    unique = list(dict.fromkeys(uids))
    if not unique:
        return {}
    db = get_firestore()
    refs = [db.collection(COMMUNITIES).document(uid) for uid in unique]
    return {snap.id: public_summary(snap.id, snap.to_dict()) for snap in db.get_all(refs) if snap.exists}


def update_community(uid: str, payload: CommunityUpdate) -> None:
    data = payload.model_dump(exclude_unset=True)
    # Same dotted-path merge trick as users_service.update_profile — a
    # partial contactPerson/address/socials object must not blank out
    # fields the client didn't send.
    contact_person = data.pop("contactPerson", None) or {}
    address = data.pop("address", None) or {}
    socials = data.pop("socials", None) or {}
    updates = {k: v for k, v in data.items() if v is not None}
    updates.update({f"contactPerson.{k}": v for k, v in contact_person.items() if v is not None})
    updates.update({f"address.{k}": v for k, v in address.items() if v is not None})
    updates.update({f"socials.{k}": v for k, v in socials.items() if v is not None})
    if not updates:
        return
    updates["updatedAt"] = firestore.SERVER_TIMESTAMP
    get_firestore().collection(COMMUNITIES).document(uid).update(updates)


def add_photo(uid: str, storage_path: str, order: int, url: str | None = None) -> None:
    ref = get_firestore().collection(COMMUNITIES).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []
    if len(photos) >= MAX_PHOTOS:
        raise ValueError(f"Maximum of {MAX_PHOTOS} photos allowed")
    photo = {"storagePath": storage_path, "order": order}
    if url:
        photo["url"] = url
    ref.update(
        {
            "photos": firestore.ArrayUnion([photo]),
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }
    )


def reorder_photos(uid: str, storage_paths: list[str]) -> None:
    ref = get_firestore().collection(COMMUNITIES).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []

    by_path = {p.get("storagePath"): p for p in photos}
    if sorted(storage_paths) != sorted(by_path.keys()):
        raise ValueError("storagePaths must match your existing photos exactly")

    reordered = [{**by_path[path], "storagePath": path, "order": i} for i, path in enumerate(storage_paths)]
    ref.update({"photos": reordered, "updatedAt": firestore.SERVER_TIMESTAMP})


def remove_photo(uid: str, storage_path: str) -> None:
    ref = get_firestore().collection(COMMUNITIES).document(uid)
    snap = ref.get()
    photos = snap.to_dict().get("photos", []) if snap.exists else []
    remaining = [p for p in photos if p.get("storagePath") != storage_path]
    ref.update({"photos": remaining, "updatedAt": firestore.SERVER_TIMESTAMP})


def _membership_refs(db, cid: str, uid: str):
    return (
        db.collection(COMMUNITIES).document(cid).collection(MEMBERS).document(uid),
        db.collection("users").document(uid).collection(MEMBERSHIPS).document(cid),
    )


def _joined_set(db, uid: str, cids: list[str]) -> set[str]:
    """Which of `cids` the caller (uid) already belongs to, via one batch read
    of their own memberships subcollection — never the community's member
    list, which stays private to the community."""
    if not cids:
        return set()
    refs = [db.collection("users").document(uid).collection(MEMBERSHIPS).document(cid) for cid in cids]
    # get_all doesn't preserve ref order, so key by the doc's own id rather
    # than zipping against cids (same convention as _blocked_uids elsewhere).
    return {snap.id for snap in db.get_all(refs) if snap.exists}


def join_community(cid: str, uid: str) -> None:
    db = get_firestore()
    community_ref = db.collection(COMMUNITIES).document(cid)
    community_snap = community_ref.get()
    if not community_snap.exists:
        raise NotFoundError("Community not found")

    member_ref, membership_ref = _membership_refs(db, cid, uid)
    if member_ref.get().exists:
        return  # already a member — idempotent, memberCount must not double-count

    batch = db.batch()
    batch.set(member_ref, {"createdAt": firestore.SERVER_TIMESTAMP})
    batch.set(
        membership_ref,
        {"communityName": community_snap.to_dict().get("name"), "createdAt": firestore.SERVER_TIMESTAMP},
    )
    batch.update(community_ref, {"memberCount": firestore.Increment(1)})
    batch.commit()


def leave_community(cid: str, uid: str) -> None:
    db = get_firestore()
    member_ref, membership_ref = _membership_refs(db, cid, uid)
    if not member_ref.get().exists:
        return  # not a member — idempotent

    batch = db.batch()
    batch.delete(member_ref)
    batch.delete(membership_ref)
    community_ref = db.collection(COMMUNITIES).document(cid)
    if community_ref.get().exists:
        batch.update(community_ref, {"memberCount": firestore.Increment(-1)})
    batch.commit()


def list_directory(uid: str, limit: int = 50) -> list[dict]:
    """Verified communities, biggest first, with `joined` for the caller."""
    db = get_firestore()
    query = (
        db.collection(COMMUNITIES)
        .where("verification", "==", "verified")
        .order_by("memberCount", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    docs = list(query.stream())
    joined = _joined_set(db, uid, [doc.id for doc in docs])
    return [{**public_summary(doc.id, doc.to_dict()), "joined": doc.id in joined} for doc in docs]


def get_community_card(uid: str, cid: str) -> dict | None:
    """A single verified community's public card, or None if it doesn't exist
    or isn't verified yet — pending communities aren't publicly visible."""
    community = get_community(cid)
    if not community or community.get("verification") != "verified":
        return None
    joined = cid in _joined_set(get_firestore(), uid, [cid])
    return {**public_summary(cid, community), "joined": joined}


def get_verified_uids(cids: list[str]) -> set[str]:
    """Which of `cids` are currently verified communities, via one batch read."""
    unique = list(dict.fromkeys(cids))
    if not unique:
        return set()
    db = get_firestore()
    refs = [db.collection(COMMUNITIES).document(cid) for cid in unique]
    return {
        snap.id
        for snap in db.get_all(refs)
        if snap.exists and snap.to_dict().get("verification") == "verified"
    }


def get_joined_community_ids(uid: str) -> list[str]:
    db = get_firestore()
    return [doc.id for doc in db.collection("users").document(uid).collection(MEMBERSHIPS).stream()]


def list_my_communities(uid: str) -> list[dict]:
    cids = get_joined_community_ids(uid)
    if not cids:
        return []
    communities = get_public_communities(cids)
    return [{**communities[cid], "joined": True} for cid in cids if cid in communities]


def is_member_or_self(uid: str, cid: str) -> bool:
    """Whether `uid` may see `cid`'s member list — either the community
    itself, or someone who has joined it. Member lists otherwise stay private
    (this app hosts organizing communities where a public roster is a real
    safety risk for a diaspora audience)."""
    if uid == cid:
        return True
    return get_firestore().collection(COMMUNITIES).document(cid).collection(MEMBERS).document(uid).get().exists


def list_members(cid: str, limit: int = 50) -> list[dict]:
    """A community's members, oldest-joined first, as slim profiles — not the
    full dating-card view (bio/socials/prompts stay out; being in the same
    community shouldn't expose more than a name, photo, and region)."""
    from app.services import users as users_service

    db = get_firestore()
    query = (
        db.collection(COMMUNITIES)
        .document(cid)
        .collection(MEMBERS)
        .order_by("createdAt")
        .limit(limit)
    )
    member_uids = [doc.id for doc in query.stream()]
    profiles = users_service.get_public_profiles(member_uids)

    members = []
    for uid in member_uids:
        profile = profiles.get(uid)
        if not profile:
            continue
        photos = profile.get("photos") or []
        members.append(
            {
                "uid": uid,
                "displayName": profile.get("displayName"),
                "photo": photos[0] if photos else None,
                "region": profile.get("region"),
            }
        )
    return members


def delete_community(uid: str) -> None:
    """Remove the community's data and Firebase Auth account.

    Membership marker docs (communities/{uid}/members/*, and each member's
    users/{memberUid}/memberships/{uid}) and the community's posts are left
    for a background cleanup — deleting them synchronously here could be an
    unbounded fan-out for a large community. This mirrors how ads/photos are
    handled elsewhere: correctness now, cleanup can be a follow-up job.
    """
    from app.services import storage as storage_service

    db = get_firestore()
    ref = db.collection(COMMUNITIES).document(uid)
    snap = ref.get()
    community = snap.to_dict() if snap.exists else {}

    for photo in community.get("photos", []):
        path = photo.get("storagePath")
        if path:
            storage_service.delete_blob(path)

    ref.delete()

    ensure_app()
    try:
        firebase_auth.delete_user(uid)
    except firebase_auth.UserNotFoundError:
        pass
