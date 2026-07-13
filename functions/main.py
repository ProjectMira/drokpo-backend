import html
import io
import os
import uuid

from firebase_admin import firestore, initialize_app, messaging, storage
from firebase_functions import firestore_fn, options, storage_fn
from google.api_core import exceptions as google_exceptions
from PIL import Image, ImageOps

initialize_app()

# Profile cards render at phone-screen sizes; a 1080px-longest-edge JPEG is
# visually identical there but ~10-30x smaller than a raw camera upload.
MAX_PHOTO_DIM = 1080
JPEG_QUALITY = 82
# Must match PHOTO_CACHE_CONTROL in backend/app/services/storage.py.
PHOTO_CACHE_CONTROL = "public, max-age=2592000"

# Who gets notified when a new community registers — see docs/COMMUNITIES.md
# for the full registration -> approval email loop.
ADMIN_EMAIL = os.environ.get("DROKPO_ADMIN_EMAIL", "ta3tsering@gmail.com")


@storage_fn.on_object_finalized(memory=options.MemoryOption.MB_512)
def optimize_photo(event: storage_fn.CloudEvent[storage_fn.StorageObjectData]) -> None:
    """Downscale and re-encode uploaded photos in place.

    The storagePath — and therefore any download URL/token the backend has
    already stored — stays identical; only the bytes get smaller. Users keep
    uploading straight from camera (up to the 10MB rule limit) and viewers
    download a phone-sized JPEG instead.
    """
    obj = event.data
    name = obj.name or ""
    is_profile_photo = name.startswith("users/") and "/photos/" in name
    is_community_photo = name.startswith("communities/") and "/photos/" in name
    if not is_profile_photo and not is_community_photo and not name.startswith("ads/"):
        return
    if not (obj.content_type or "").startswith("image/"):
        return
    if (obj.metadata or {}).get("drokpoOptimized"):
        # Our own rewrite re-fires this trigger; the marker breaks the loop.
        return

    bucket = storage.bucket(obj.bucket)
    for _ in range(2):
        blob = bucket.get_blob(name)
        if blob is None:
            return
        try:
            image = Image.open(io.BytesIO(blob.download_as_bytes()))
            # Bake the EXIF rotation into the pixels before it's stripped.
            image = ImageOps.exif_transpose(image)
        except Exception:
            return  # not decodable (e.g. HEIC without codec) — leave it alone
        image.thumbnail((MAX_PHOTO_DIM, MAX_PHOTO_DIM))  # no-op if already small
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)

        # Keep any download token the backend minted between upload and now,
        # and mint one ourselves otherwise so the token never changes again.
        metadata = dict(blob.metadata or {})
        metadata.setdefault("firebaseStorageDownloadTokens", str(uuid.uuid4()))
        metadata["drokpoOptimized"] = "true"
        blob.metadata = metadata
        blob.cache_control = PHOTO_CACHE_CONTROL
        blob.content_type = "image/jpeg"
        try:
            # Generation preconditions close the race with the backend's
            # attach-time metadata patch: if the blob changed since we read
            # it, retry with the fresh token instead of clobbering it.
            blob.upload_from_string(
                out.getvalue(),
                content_type="image/jpeg",
                if_generation_match=blob.generation,
                if_metageneration_match=blob.metageneration,
            )
            return
        except google_exceptions.PreconditionFailed:
            continue


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


# --- Community registration emails ------------------------------------------
#
# Delivery is the Firebase "Trigger Email" extension (firebase/firestore-
# send-email), watching the `mail` collection — a one-time manual install
# with SMTP credentials, documented in docs/COMMUNITIES.md. Until that
# extension is installed, docs written here simply queue harmlessly.


def _esc(value) -> str:
    # Community-supplied fields (name, description, ...) are untrusted input
    # embedded into an HTML email; escape before interpolating.
    return html.escape(str(value)) if value else ""


def _queue_email(db, to: str, subject: str, body_html: str) -> None:
    db.collection("mail").add({"to": [to], "message": {"subject": subject, "html": body_html}})


def _registration_email_html(uid: str, data: dict) -> str:
    contact = data.get("contactPerson") or {}
    address = data.get("address") or {}
    address_line = ", ".join(
        filter(None, [address.get("line1"), address.get("city"), address.get("state"),
                       address.get("country"), address.get("postalCode")])
    )
    rows = [
        ("Name", data.get("name")),
        ("Description", data.get("description")),
        ("Website", data.get("website")),
        ("Phone", data.get("phone")),
        ("Email", data.get("email")),
        ("Contact person", contact.get("name")),
        ("Contact role", contact.get("role")),
        ("Contact phone", contact.get("phone")),
        ("Contact email", contact.get("email")),
        ("Address", address_line),
    ]
    rows_html = "".join(
        f"<tr><td><b>{_esc(label)}</b></td><td>{_esc(value)}</td></tr>" for label, value in rows if value
    )
    console_url = (
        f"https://console.firebase.google.com/project/drokpo-backend/firestore/data/~2Fcommunities~2F{uid}"
    )
    return (
        "<p>A new community registered on Drokpo and is awaiting review.</p>"
        f"<table>{rows_html}</table>"
        f'<p><a href="{_esc(console_url)}">Open in the Firebase console</a> — set '
        f'<code>verification</code> to <code>"verified"</code> to approve it '
        '(see docs/COMMUNITIES.md).</p>'
    )


@firestore_fn.on_document_created(document="communities/{uid}")
def on_community_created(event: firestore_fn.Event) -> None:
    data = event.data.to_dict() or {}
    uid = event.params["uid"]
    db = firestore.client()
    try:
        _queue_email(
            db,
            ADMIN_EMAIL,
            f"New community registration: {data.get('name', uid)}",
            _registration_email_html(uid, data),
        )
    except Exception:
        # Registration itself already succeeded; a queueing failure here
        # must never surface as an error to the registering community.
        pass


@firestore_fn.on_document_updated(document="communities/{uid}")
def on_community_verification_changed(event: firestore_fn.Event) -> None:
    before = event.data.before.to_dict() if event.data.before else {}
    after = event.data.after.to_dict() if event.data.after else {}
    before_status, after_status = (before or {}).get("verification"), (after or {}).get("verification")
    if before_status == after_status:
        return

    email = (after or {}).get("email")
    if not email:
        return  # nothing to send to (shouldn't happen for new communities — email is required)

    name = (after or {}).get("name") or "Your community"
    db = firestore.client()
    try:
        if after_status == "verified":
            _queue_email(
                db,
                email,
                f"{name} is approved on Drokpo",
                f"<p>Good news — <b>{_esc(name)}</b> is now verified on Drokpo. You can publish "
                "posts, and your community now appears in the directory and Discover feed.</p>",
            )
        elif after_status == "rejected":
            _queue_email(
                db,
                email,
                "Update on your Drokpo community registration",
                f"<p>We weren't able to approve <b>{_esc(name)}</b>'s registration on Drokpo at "
                "this time. Reply to this email if you have questions.</p>",
            )
    except Exception:
        pass
