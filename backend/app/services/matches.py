from app.firebase import get_firestore

MATCHES = "matches"


def list_for_user(uid: str) -> list[dict]:
    from app.services import counterparts as counterparts_service

    db = get_firestore()
    query = db.collection(MATCHES).where("users", "array_contains", uid).where("status", "==", "active")
    matches = [{"matchId": doc.id, **doc.to_dict()} for doc in query.stream()]

    # Join the other participant's public profile (person or community) so
    # the client can render the match list without per-user follow-up
    # requests.
    other_uids = [next((u for u in m.get("users", []) if u != uid), None) for m in matches]
    profiles = counterparts_service.get_public_counterparts([u for u in other_uids if u])
    return [
        {**match, "otherUser": profiles[other_uid]}
        for match, other_uid in zip(matches, other_uids)
        if other_uid in profiles
    ]


def unmatch(match_id: str, uid: str) -> None:
    db = get_firestore()
    ref = db.collection(MATCHES).document(match_id)
    snap = ref.get()
    if not snap.exists or uid not in snap.to_dict().get("users", []):
        raise ValueError("Match not found")
    ref.update({"status": "unmatched"})
