"""One-off backfill: stamp `uid` on every poll vote, comment vote and RSVP
written before those docs carried it.

Account deletion finds a member's votes and RSVPs with collection-group
queries on `uid` (app/services/account_deletion.py). Docs from before the
field existed are keyed by the uid but don't contain it, so those queries
can't see them. Run this once after the `votes.uid` / `rsvps.uid` index
overrides in firestore.indexes.json are deployed.

Run from backend/ with admin credentials:

    FIREBASE_PROJECT_ID=<project> STORAGE_BUCKET=<bucket> \
    GOOGLE_APPLICATION_CREDENTIALS=<service-account.json> \
        python -m scripts.backfill_vote_uids

Idempotent: docs that already have a uid are left untouched.
"""

from app.firebase import get_firestore
from app.services.account_deletion import BATCH_CHUNK_SIZE
from app.services.communityposts import RSVPS, VOTES


def main() -> None:
    db = get_firestore()
    for group in (VOTES, RSVPS):
        scanned = updated = 0
        batch, pending = db.batch(), 0
        for snap in db.collection_group(group).stream():
            scanned += 1
            if (snap.to_dict() or {}).get("uid"):
                continue
            batch.update(snap.reference, {"uid": snap.id})
            pending += 1
            updated += 1
            if pending == BATCH_CHUNK_SIZE:
                batch.commit()
                batch, pending = db.batch(), 0
        if pending:
            batch.commit()
        print(f"{group}: {updated} of {scanned} docs backfilled")


if __name__ == "__main__":
    main()
