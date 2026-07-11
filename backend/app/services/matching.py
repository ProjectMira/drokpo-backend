from firebase_admin import firestore

from app.firebase import get_firestore


class BlockedError(Exception):
    pass


def _match_id(uid_a: str, uid_b: str) -> str:
    return "_".join(sorted([uid_a, uid_b]))


def _either_blocked(db, uid_a: str, uid_b: str) -> bool:
    refs = [
        db.collection("blocks").document(uid_a).collection("blockedUsers").document(uid_b),
        db.collection("blocks").document(uid_b).collection("blockedUsers").document(uid_a),
    ]
    return any(snap.exists for snap in db.get_all(refs))


@firestore.transactional
def _swipe_transaction(transaction, db, from_uid: str, to_uid: str, action: str) -> str | None:
    # Firestore transactions require all reads before any writes, so the
    # reciprocal-like check has to happen first.
    reverse_liked = False
    if action in ("like", "superlike"):
        reverse_ref = db.collection("users").document(to_uid).collection("swipes").document(from_uid)
        reverse_snap = reverse_ref.get(transaction=transaction)
        reverse_liked = reverse_snap.exists and reverse_snap.to_dict().get("action") in ("like", "superlike")

    match_id = None
    match_ref = None
    existing_status = None
    if reverse_liked:
        match_id = _match_id(from_uid, to_uid)
        match_ref = db.collection("matches").document(match_id)
        match_snap = match_ref.get(transaction=transaction)
        if match_snap.exists:
            existing_status = match_snap.to_dict().get("status")

    swipe_ref = db.collection("users").document(from_uid).collection("swipes").document(to_uid)
    # fromUid/toUid are denormalized into the doc so "likes you received" can
    # be answered with a collection-group query on toUid.
    transaction.set(
        swipe_ref,
        {
            "action": action,
            "fromUid": from_uid,
            "toUid": to_uid,
            "createdAt": firestore.SERVER_TIMESTAMP,
        },
    )

    if not reverse_liked:
        return None

    if existing_status is None:
        transaction.set(
            match_ref,
            {
                "users": sorted([from_uid, to_uid]),
                "status": "active",
                "createdAt": firestore.SERVER_TIMESTAMP,
                "lastMessage": None,
                "unreadCount": {from_uid: 0, to_uid: 0},
            },
        )
        return match_id

    # A match doc already exists. Only report a match if it is still active;
    # an "unmatched" doc means one side ended it, and a re-like must not
    # resurrect it or tell the client a fresh match happened.
    return match_id if existing_status == "active" else None


def record_swipe(from_uid: str, to_uid: str, action: str) -> str | None:
    db = get_firestore()
    if _either_blocked(db, from_uid, to_uid):
        raise BlockedError("Cannot swipe on this user")
    transaction = db.transaction()
    return _swipe_transaction(transaction, db, from_uid, to_uid, action)


def list_swipes(uid: str, action: str | None = None, limit: int = 100) -> list[dict]:
    db = get_firestore()
    query = db.collection("users").document(uid).collection("swipes")
    if action:
        query = query.where("action", "==", action)
    swipes = [{"uid": doc.id, **doc.to_dict()} for doc in query.limit(limit).stream()]
    return _attach_match_state(db, uid, _attach_profiles(swipes))


def list_received(uid: str, action: str | None = None, limit: int = 100) -> list[dict]:
    """Swipes other users made on `uid` — e.g. the likes you received.

    Collection-group query on the denormalized toUid field; needs the
    COLLECTION_GROUP field override declared in firestore.indexes.json.
    """
    db = get_firestore()
    query = db.collection_group("swipes").where("toUid", "==", uid).limit(limit)
    received = []
    for doc in query.stream():
        data = doc.to_dict()
        # Filter by action here rather than in the query so the single
        # toUid index covers every variant.
        if action and data.get("action") != action:
            continue
        received.append({"uid": data.get("fromUid", doc.reference.parent.parent.id), **data})
    return _attach_match_state(db, uid, _attach_profiles(received))


def _attach_profiles(swipes: list[dict]) -> list[dict]:
    """Join each swipe's counterpart profile; drops swipes whose profile is gone."""
    from app.services import users as users_service

    profiles = users_service.get_public_profiles([s["uid"] for s in swipes])
    return [{**s, "otherUser": profiles[s["uid"]]} for s in swipes if s["uid"] in profiles]


def _attach_match_state(db, uid: str, swipes: list[dict]) -> list[dict]:
    """Tell the client whether each swipe's counterpart is an active match, so
    it can offer "Send message" instead of "Like back"."""
    if not swipes:
        return swipes
    refs = [db.collection("matches").document(_match_id(uid, s["uid"])) for s in swipes]
    status_by_id = {snap.id: (snap.to_dict() or {}).get("status") for snap in db.get_all(refs) if snap.exists}
    for s in swipes:
        match_id = _match_id(uid, s["uid"])
        status = status_by_id.get(match_id)
        s["matchId"] = match_id if status == "active" else None
        s["matchStatus"] = status
    return swipes
