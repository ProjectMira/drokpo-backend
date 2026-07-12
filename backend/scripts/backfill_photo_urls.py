"""One-off backfill: resolve a download URL (and Cache-Control metadata) for
every photo attached before the backend started storing `url` on the photo map.

New attaches get their URL at confirm time (routers/common.py attach_photo);
this brings existing profiles up to the same shape so the app can drop its
per-photo getDownloadURL() calls entirely.

Run from backend/ with admin credentials:

    FIREBASE_PROJECT_ID=<project> STORAGE_BUCKET=<bucket> \
    GOOGLE_APPLICATION_CREDENTIALS=<service-account.json> \
        python -m scripts.backfill_photo_urls

Idempotent — photos that already have a url are left untouched.
"""

from app.firebase import get_firestore
from app.services import storage as storage_service


def main() -> None:
    db = get_firestore()
    scanned = updated = 0
    for snap in db.collection("users").stream():
        scanned += 1
        photos = (snap.to_dict() or {}).get("photos") or []
        changed = False
        backfilled = []
        for photo in photos:
            if not photo.get("url") and photo.get("storagePath"):
                url = storage_service.ensure_download_url(photo["storagePath"])
                if url:
                    photo = {**photo, "url": url}
                    changed = True
            backfilled.append(photo)
        if changed:
            snap.reference.update({"photos": backfilled})
            updated += 1
            print(f"backfilled {snap.id} ({len(backfilled)} photos)")
    print(f"done: {updated} of {scanned} users updated")


if __name__ == "__main__":
    main()
