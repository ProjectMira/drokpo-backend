from app.firebase import get_firestore

MATCHES = "matches"


def list_for_user(uid: str) -> list[dict]:
    db = get_firestore()
    query = db.collection(MATCHES).where("users", "array_contains", uid).where("status", "==", "active")
    return [{"matchId": doc.id, **doc.to_dict()} for doc in query.stream()]


def unmatch(match_id: str, uid: str) -> None:
    db = get_firestore()
    ref = db.collection(MATCHES).document(match_id)
    snap = ref.get()
    if not snap.exists or uid not in snap.to_dict().get("users", []):
        raise ValueError("Match not found")
    ref.update({"status": "unmatched"})
