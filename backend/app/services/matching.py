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
    transaction.set(swipe_ref, {"action": action, "createdAt": firestore.SERVER_TIMESTAMP})

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
    return [{"uid": doc.id, **doc.to_dict()} for doc in query.limit(limit).stream()]
