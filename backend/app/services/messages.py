from firebase_admin import firestore

from app.firebase import get_firestore

MATCHES = "matches"
MESSAGES = "messages"


class NotParticipantError(Exception):
    pass


class MatchClosedError(Exception):
    pass


def _require_participant(db, match_id: str, uid: str) -> dict:
    snap = db.collection(MATCHES).document(match_id).get()
    if not snap.exists or uid not in snap.to_dict().get("users", []):
        raise NotParticipantError("Match not found")
    return snap.to_dict()


def send_message(
    match_id: str,
    uid: str,
    text: str | None,
    image_url: str | None = None,
    audio_url: str | None = None,
    audio_duration_sec: int | None = None,
) -> str:
    db = get_firestore()
    match = _require_participant(db, match_id, uid)
    if match.get("status") != "active":
        raise MatchClosedError("This conversation has ended")
    _, ref = db.collection(MATCHES).document(match_id).collection(MESSAGES).add(
        {
            "senderId": uid,
            "text": text,
            "imageUrl": image_url,
            "audioUrl": audio_url,
            "audioDurationSec": audio_duration_sec,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "readAt": None,
        }
    )
    # lastMessage/unreadCount denormalization and the FCM push are handled by
    # the on_message_created Cloud Function, same as for client-direct writes.
    return ref.id


def list_messages(match_id: str, uid: str, limit: int = 30, before: str | None = None) -> list[dict]:
    db = get_firestore()
    _require_participant(db, match_id, uid)
    messages = db.collection(MATCHES).document(match_id).collection(MESSAGES)
    query = messages.order_by("createdAt", direction=firestore.Query.DESCENDING)
    if before:
        cursor = messages.document(before).get()
        if cursor.exists:
            query = query.start_after(cursor)
    return [{"messageId": doc.id, **doc.to_dict()} for doc in query.limit(limit).stream()]


def mark_read(match_id: str, uid: str) -> None:
    db = get_firestore()
    _require_participant(db, match_id, uid)
    db.collection(MATCHES).document(match_id).update({f"unreadCount.{uid}": 0})


def list_sent(uid: str, limit: int = 50) -> list[dict]:
    # Collection-group query across every matches/*/messages subcollection;
    # needs the COLLECTION_GROUP index declared in firestore.indexes.json.
    db = get_firestore()
    query = (
        db.collection_group(MESSAGES)
        .where("senderId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    sent = []
    for doc in query.stream():
        match_ref = doc.reference.parent.parent
        sent.append({"messageId": doc.id, "matchId": match_ref.id, **doc.to_dict()})
    return sent
