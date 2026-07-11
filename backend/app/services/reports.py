from firebase_admin import firestore

from app.firebase import get_firestore
from app.models.report import ReportIn
from app.services.matching import _match_id


def create_report(reporter_uid: str, payload: ReportIn) -> None:
    db = get_firestore()
    db.collection("reports").add(
        {
            "reporterUid": reporter_uid,
            "reportedUid": payload.reportedUid,
            "reason": payload.reason,
            "note": payload.note,
            "status": "open",
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
    )


def _block_refs(db, uid: str, target_uid: str):
    # Blocks are mirrored so both sides can filter their feed with a single
    # subcollection read: the blocker's blockedUsers list and the target's
    # blockedBy list.
    return (
        db.collection("blocks").document(uid).collection("blockedUsers").document(target_uid),
        db.collection("blocks").document(target_uid).collection("blockedBy").document(uid),
    )


def block_user(uid: str, target_uid: str) -> None:
    db = get_firestore()
    blocked_ref, blocked_by_ref = _block_refs(db, uid, target_uid)
    batch = db.batch()
    batch.set(blocked_ref, {"createdAt": firestore.SERVER_TIMESTAMP})
    batch.set(blocked_by_ref, {"createdAt": firestore.SERVER_TIMESTAMP})
    batch.commit()

    # Blocking ends any conversation: flip the deterministic match doc to
    # "unmatched" if it's still active, so the blocked person can't keep
    # messaging (match ids are sorted-uid pairs, shared with matching.py).
    match_ref = db.collection("matches").document(_match_id(uid, target_uid))
    match_snap = match_ref.get()
    if match_snap.exists and match_snap.to_dict().get("status") == "active":
        match_ref.update({"status": "unmatched"})


def unblock_user(uid: str, target_uid: str) -> None:
    db = get_firestore()
    blocked_ref, blocked_by_ref = _block_refs(db, uid, target_uid)
    batch = db.batch()
    batch.delete(blocked_ref)
    batch.delete(blocked_by_ref)
    batch.commit()
