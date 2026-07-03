from firebase_admin import firestore, initialize_app, messaging
from firebase_functions import firestore_fn

initialize_app()


def _tokens_for(db, uids: list[str]) -> list[str]:
    tokens = []
    for uid in uids:
        snap = db.collection("users").document(uid).get()
        if snap.exists:
            tokens.extend(snap.to_dict().get("fcmTokens", []))
    return tokens


def _send(tokens: list[str], title: str, body: str) -> None:
    if not tokens:
        return
    messaging.send_each_for_multicast(
        messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            tokens=tokens,
        )
    )


@firestore_fn.on_document_created(document="matches/{matchId}")
def on_match_created(event: firestore_fn.Event) -> None:
    data = event.data.to_dict() or {}
    db = firestore.client()
    tokens = _tokens_for(db, data.get("users", []))
    _send(tokens, "It's a match!", "You have a new match on Changsa.")


@firestore_fn.on_document_created(document="matches/{matchId}/messages/{messageId}")
def on_message_created(event: firestore_fn.Event) -> None:
    message = event.data.to_dict() or {}
    match_id = event.params["matchId"]
    db = firestore.client()

    match_ref = db.collection("matches").document(match_id)
    match_snap = match_ref.get()
    if not match_snap.exists:
        return
    match_data = match_snap.to_dict()

    sender_id = message.get("senderId")
    recipients = [uid for uid in match_data.get("users", []) if uid != sender_id]

    match_ref.update(
        {
            "lastMessage": {
                "text": message.get("text", ""),
                "senderId": sender_id,
                "createdAt": message.get("createdAt"),
            },
            **{f"unreadCount.{uid}": firestore.Increment(1) for uid in recipients},
        }
    )

    tokens = _tokens_for(db, recipients)
    _send(tokens, "New message", message.get("text", "")[:100])
