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


def _send(tokens: list[str], title: str, body: str, data: dict[str, str] | None = None) -> None:
    if not tokens:
        return
    messaging.send_each_for_multicast(
        messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=data or {},
            tokens=tokens,
        )
    )


@firestore_fn.on_document_created(document="matches/{matchId}")
def on_match_created(event: firestore_fn.Event) -> None:
    data = event.data.to_dict() or {}
    match_id = event.params["matchId"]
    db = firestore.client()
    tokens = _tokens_for(db, data.get("users", []))
    _send(
        tokens,
        "You made a new friend!",
        "You have a new connection on Drokpo — say tashi delek!",
        {"type": "match", "matchId": match_id},
    )


@firestore_fn.on_document_created(document="users/{uid}/swipes/{targetUid}")
def on_swipe_created(event: firestore_fn.Event) -> None:
    data = event.data.to_dict() or {}
    if data.get("action") not in ("like", "superlike"):
        return
    from_uid = data.get("fromUid") or event.params["uid"]
    to_uid = data.get("toUid") or event.params["targetUid"]

    db = firestore.client()

    # If this like completed a match, the match doc was written in the same
    # transaction as this swipe, so it's already readable here. Skip the like
    # push so the recipient doesn't get both a "likes you" and a match push.
    match_id = "_".join(sorted([from_uid, to_uid]))
    if db.collection("matches").document(match_id).get().exists:
        return

    # Respect blocks in either direction (mirrors matching._either_blocked).
    refs = [
        db.collection("blocks").document(from_uid).collection("blockedUsers").document(to_uid),
        db.collection("blocks").document(to_uid).collection("blockedUsers").document(from_uid),
    ]
    if any(snap.exists for snap in db.get_all(refs)):
        return

    tokens = _tokens_for(db, [to_uid])
    _send(
        tokens,
        "Someone likes you!",
        "Open Drokpo to see who liked you.",
        {"type": "like"},
    )


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
    _send(
        tokens,
        "New message",
        message.get("text", "")[:100],
        {"type": "message", "matchId": match_id},
    )
