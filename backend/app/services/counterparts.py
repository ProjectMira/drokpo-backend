"""Role-aware counterpart lookups for the matching/chat pipeline.

Swipes, likes, and matches all join "the other uid" to a public profile so
the client can render a name/photo without a follow-up request. Community
accounts now participate in that same pipeline (they swipe/match/chat as
themselves — see PLAN-tab-restructure-community-participation.md), so the
join has to check `communities/{uid}` whenever `users/{uid}` doesn't exist,
and map the result into the same FeedCard-ish shape the app already decodes
for a person, plus an additive `kind` discriminator. Old app builds that
don't know about `kind` still get a well-formed name + photo instead of a
dropped/blank row.
"""

from app.firebase import get_firestore
from app.services import communities as communities_service
from app.services import users as users_service


def _community_as_counterpart(cid: str, data: dict) -> dict:
    address = data.get("address") or {}
    region = ", ".join(filter(None, [address.get("city"), address.get("country")])) or None
    return {
        "uid": cid,
        "kind": "community",
        "displayName": data.get("name"),
        "bio": data.get("description"),
        "region": region,
        "photos": data.get("photos", []),
        "socials": data.get("socials"),
        "verification": data.get("verification"),
        "memberCount": data.get("memberCount"),
    }


def get_public_counterparts(uids: list[str]) -> dict[str, dict]:
    """Batch-resolve `uids` to public views, keyed by uid. Persons come from
    `users` (kind="person"); any uid not found there is looked up in
    `communities` and mapped to a counterpart card (kind="community"). A uid
    in neither collection (deleted account) is simply absent from the result,
    same as the person-only join this replaces."""
    unique = list(dict.fromkeys(uids))
    if not unique:
        return {}

    profiles = users_service.get_public_profiles(unique)
    result = {uid: {**data, "kind": "person"} for uid, data in profiles.items()}

    missing = [uid for uid in unique if uid not in result]
    if missing:
        db = get_firestore()
        refs = [db.collection(communities_service.COMMUNITIES).document(uid) for uid in missing]
        for snap in db.get_all(refs):
            if snap.exists:
                result[snap.id] = _community_as_counterpart(snap.id, snap.to_dict())

    return result
