"""Account deletion: everything an account leaves outside its own doc.

Shared by DELETE /api/profile/me (users.delete_account) and DELETE
/api/communities/me (communities.delete_community). Community accounts swipe,
match, chat, comment, vote and like as themselves, so both roles leave the
same trail. Firestore never cascades, so every kind of doc is found
explicitly, either through a collection-group query on a uid field
(swipes.toUid, messages.senderId, comments.authorUid, votes.uid, rsvps.uid)
or through the account's own mirror docs (memberships, blocks). It is then
removed in batched writes.

Each step is idempotent, and the account's own doc (the one the role check
in app/dependencies.py looks for) is deleted last. So when a deletion fails
partway with a 500, the app can retry it and the retry picks up whatever is
left.
"""

from firebase_admin import firestore

from app.firebase import get_firestore
from app.services import storage as storage_service
from app.services.comments import COMMENTS
from app.services.communities import COMMUNITIES, MEMBERS, MEMBERSHIPS
from app.services.communityposts import COMMUNITY_POSTS, RSVPS, VOTES

USERS = "users"
MATCHES = "matches"
MESSAGES = "messages"
# Firestore batch writes cap at 500 operations; same headroom as
# comments.BATCH_CHUNK_SIZE.
BATCH_CHUNK_SIZE = 400

# What a top-level comment becomes when its author deletes their account but
# other people's replies still hang off it. Deleting it would take those
# replies with it (comments.delete_comment cascades), and they aren't the
# deleted account's to remove. The app renders authorName in the header and
# text as the body; every field that identified the author is cleared.
COMMENT_TOMBSTONE = {
    "authorUid": None,
    "authorKind": None,
    "authorName": "Deleted account",
    "authorPhotoUrl": None,
    "text": "This comment was deleted.",
    "audioUrl": None,
    "audioDurationSec": None,
    "deleted": True,
}


def delete_person(uid: str) -> None:
    """Delete everything a person account owns or left behind, except its
    Firebase Auth user (the caller removes that once this returns)."""
    db = get_firestore()
    _release_memberships(db, uid)
    _delete_participant_data(db, uid)
    storage_service.delete_prefix(f"{USERS}/{uid}/")
    # Last: users/{uid} is what require_person_uid checks, so while it exists
    # a failed deletion can still be retried. Its subcollections (swipes,
    # likedNews, likedPosts, memberships) go with it.
    _delete_tree(db, db.collection(USERS).document(uid))


def delete_community(cid: str) -> None:
    """Delete everything a community account owns or left behind, except its
    Firebase Auth user (the caller removes that once this returns)."""
    db = get_firestore()
    _delete_posts(db, cid)
    _release_members(db, cid)
    _delete_participant_data(db, cid)
    storage_service.delete_prefix(f"{COMMUNITIES}/{cid}/")
    # A community's swipes and liked content are stored under users/{cid},
    # the same path a person's are, even though no users/{cid} doc exists.
    _delete_tree(db, db.collection(USERS).document(cid))
    # Last, for the same retry reason as delete_person: communities/{cid} is
    # what require_community_uid checks.
    _delete_tree(db, db.collection(COMMUNITIES).document(cid))


def _delete_participant_data(db, uid: str) -> None:
    """The trail both roles leave by swiping, blocking, voting, commenting
    and chatting."""
    _delete_received_swipes(db, uid)
    _delete_blocks(db, uid)
    # Votes before comments: retracting the account's vote on its own comment
    # needs that comment to still exist.
    _retract_votes_and_rsvps(db, uid)
    _delete_comments(db, uid)
    kept_media = _close_conversations(db, uid)
    storage_service.delete_prefix(f"chatMedia/{uid}/", keep=kept_media)
    storage_service.delete_prefix(f"commentAudio/{uid}/")


# --- batched writes -----------------------------------------------------------


def _commit(db, groups: list[list[tuple]]) -> None:
    """Commit groups of ("delete", ref) / ("update", ref, fields) writes in
    batches of at most BATCH_CHUNK_SIZE, never splitting a group.

    A group pairs a delete with the counter it moves (a vote and its poll
    count, a membership and memberCount). The two land together or not at
    all, so a retried deletion can't move a counter twice."""
    batch, size = db.batch(), 0
    for group in groups:
        if size and size + len(group) > BATCH_CHUNK_SIZE:
            batch.commit()
            batch, size = db.batch(), 0
        for op, ref, *fields in group:
            if op == "delete":
                batch.delete(ref)
            else:
                batch.update(ref, fields[0])
        size += len(group)
    if size:
        batch.commit()


def _existing_paths(db, refs) -> set[str]:
    """Paths of the refs whose docs exist, via one batch read. Keyed by path
    because get_all doesn't preserve ref order."""
    unique = list({ref.path: ref for ref in refs}.values())
    if not unique:
        return set()
    return {snap.reference.path for snap in db.get_all(unique) if snap.exists}


def _subtree_refs(doc_ref) -> list:
    """Every document beneath doc_ref, not including doc_ref itself. This is
    the same traversal Client.recursive_delete uses (one all-descendants
    query per subcollection), but the deletes go through _commit so a failed
    write raises. BulkWriter would retry it and then drop the error."""
    return [snap.reference for col in doc_ref.collections() for snap in col.recursive().select([]).stream()]


def _delete_tree(db, doc_ref) -> None:
    """Delete doc_ref and everything beneath it, descendants first."""
    _commit(db, [[("delete", ref)] for ref in _subtree_refs(doc_ref)] + [[("delete", doc_ref)]])


# --- matching and safety --------------------------------------------------------


def _delete_received_swipes(db, uid: str) -> None:
    """Swipes other accounts made on this one. The account's own swipes live
    under users/{uid}/swipes and go with that tree."""
    received = db.collection_group("swipes").where("toUid", "==", uid).select([]).stream()
    _commit(db, [[("delete", snap.reference)] for snap in received])


def _delete_blocks(db, uid: str) -> None:
    """Both sides of every block this account is part of. Blocks are mirrored
    (reports._block_refs), so the account's own two lists name every
    counterpart doc."""
    blocks = db.collection("blocks")
    own = blocks.document(uid)
    groups = []
    for snap in own.collection("blockedUsers").select([]).stream():
        mirror = blocks.document(snap.id).collection("blockedBy").document(uid)
        groups.append([("delete", snap.reference), ("delete", mirror)])
    for snap in own.collection("blockedBy").select([]).stream():
        mirror = blocks.document(snap.id).collection("blockedUsers").document(uid)
        groups.append([("delete", snap.reference), ("delete", mirror)])
    _commit(db, groups)


def _close_conversations(db, uid: str) -> set[str]:
    """End every match and delete the messages this account sent. Returns the
    chatMedia paths that must survive.

    Matches are flipped to "unmatched", not deleted, so the other person's
    own messages survive, same as a normal unmatch. The exception is evidence.
    Where the other participant has an open report against this account, the
    account's messages and their media stay, and the match is marked with
    `evidenceHold` until a moderator reviews the report and purges it by hand.
    """
    reporters = set()
    for snap in db.collection("reports").where("reportedUid", "==", uid).stream():
        report = snap.to_dict() or {}
        if report.get("status") == "open":
            reporters.add(report.get("reporterUid"))

    groups = []
    held = set()
    for match in db.collection(MATCHES).where("users", "array_contains", uid).stream():
        data = match.to_dict() or {}
        updates = {}
        if data.get("status") == "active":
            updates["status"] = "unmatched"
        if reporters & (set(data.get("users", [])) - {uid}):
            held.add(match.id)
            if not data.get("evidenceHold"):
                updates["evidenceHold"] = {"uid": uid, "since": firestore.SERVER_TIMESTAMP}
        elif (data.get("lastMessage") or {}).get("senderId") == uid:
            # The denormalized preview is a copy of the account's message text.
            updates["lastMessage"] = None
        if updates:
            groups.append([("update", match.reference, updates)])

    # Same query shape as messages.list_sent, so it is served by the existing
    # senderId + createdAt collection-group index.
    sent = (
        db.collection_group(MESSAGES)
        .where("senderId", "==", uid)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .stream()
    )
    kept_media = set()
    for message in sent:
        if message.reference.parent.parent.id in held:
            data = message.to_dict() or {}
            for url in (data.get("imageUrl"), data.get("audioUrl")):
                path = storage_service.path_from_download_url(url)
                if path:
                    kept_media.add(path)
        else:
            groups.append([("delete", message.reference)])
    _commit(db, groups)
    return kept_media


# --- communities ------------------------------------------------------------------


def _release_memberships(db, uid: str) -> None:
    """Leave every joined community, as leave_community does: drop both
    marker docs and decrement memberCount. The decrement only happens while
    the member doc still exists, so a retry can't decrement twice."""
    memberships = list(db.collection(USERS).document(uid).collection(MEMBERSHIPS).select([]).stream())
    communities = db.collection(COMMUNITIES)
    member_refs = {snap.id: communities.document(snap.id).collection(MEMBERS).document(uid) for snap in memberships}
    existing = _existing_paths(db, [*member_refs.values(), *(communities.document(cid) for cid in member_refs)])

    groups = []
    for snap in memberships:
        member_ref = member_refs[snap.id]
        community_ref = communities.document(snap.id)
        group = [("delete", snap.reference)]
        if member_ref.path in existing:
            group.append(("delete", member_ref))
            if community_ref.path in existing:
                group.append(("update", community_ref, {"memberCount": firestore.Increment(-1)}))
        groups.append(group)
    _commit(db, groups)


def _release_members(db, cid: str) -> None:
    """Drop each member's users/{uid}/memberships/{cid} mirror along with the
    member doc itself."""
    members = db.collection(COMMUNITIES).document(cid).collection(MEMBERS).select([]).stream()
    users = db.collection(USERS)
    _commit(
        db,
        [
            [("delete", snap.reference), ("delete", users.document(snap.id).collection(MEMBERSHIPS).document(cid))]
            for snap in members
        ],
    )


def _delete_posts(db, cid: str) -> None:
    """The community's posts, together with their comments, comment votes,
    poll votes and RSVPs."""
    for post in db.collection(COMMUNITY_POSTS).where("communityId", "==", cid).select([]).stream():
        _delete_tree(db, post.reference)


# --- votes, RSVPs and comments ------------------------------------------------------


def _vote_counter(target, vote: dict) -> dict | None:
    """The counter update that undoes one vote. Poll votes
    (communityPosts/{p}/votes/{uid}) and comment votes
    (communityPosts/{p}/comments/{c}/votes/{uid}) share the "votes"
    collection-group name. The doc two levels up is the post or the comment
    being voted on."""
    if target.parent.id == COMMENTS:
        field = {"like": "likeCount", "dislike": "dislikeCount"}.get(vote.get("value"))
    else:
        option_id = vote.get("optionId")
        field = f"poll.counts.{option_id}" if option_id else None
    return {field: firestore.Increment(-1)} if field else None


def _retract_votes_and_rsvps(db, uid: str) -> None:
    """Undo every poll vote, comment vote and RSVP the account cast, moving
    the counter each one contributed to. The arithmetic matches the
    vote/RSVP transactions. Their read-modify-write isn't needed here,
    because nothing else touches this account's own markers mid-deletion."""
    votes = list(db.collection_group(VOTES).where("uid", "==", uid).stream())
    rsvps = list(db.collection_group(RSVPS).where("uid", "==", uid).select([]).stream())
    existing = _existing_paths(db, [snap.reference.parent.parent for snap in [*votes, *rsvps]])

    groups = []
    for snap in votes:
        target = snap.reference.parent.parent
        group = [("delete", snap.reference)]
        counter = _vote_counter(target, snap.to_dict() or {})
        if counter and target.path in existing:
            group.append(("update", target, counter))
        groups.append(group)
    for snap in rsvps:
        post = snap.reference.parent.parent
        group = [("delete", snap.reference)]
        if post.path in existing:
            group.append(("update", post, {"attendeeCount": firestore.Increment(-1)}))
        groups.append(group)
    _commit(db, groups)


def _comment_vote_deletes(comment_ref) -> list[list[tuple]]:
    # Other members' votes on a comment that's being deleted. Left in place
    # they'd be orphans under a missing doc.
    return [[("delete", snap.reference)] for snap in comment_ref.collection(VOTES).select([]).stream()]


def _delete_comments(db, uid: str) -> None:
    """Remove everything the account wrote under community posts.

    Replies are deleted first. After that, a top-level comment that still has
    replies is by definition carrying someone else's words. It becomes a
    COMMENT_TOMBSTONE instead of being deleted, which would cascade those
    replies away. Every other top-level comment is deleted."""
    authored = list(db.collection_group(COMMENTS).where("authorUid", "==", uid).stream())
    replies = [snap for snap in authored if (snap.to_dict() or {}).get("parentId")]
    top_level = [snap for snap in authored if not (snap.to_dict() or {}).get("parentId")]

    parents = [snap.reference.parent.document(snap.to_dict()["parentId"]) for snap in replies]
    existing = _existing_paths(db, [*(snap.reference.parent.parent for snap in replies), *parents])
    groups = []
    for snap, parent in zip(replies, parents):
        post = snap.reference.parent.parent
        groups.extend(_comment_vote_deletes(snap.reference))
        group = [("delete", snap.reference)]
        if post.path in existing:
            group.append(("update", post, {"commentCount": firestore.Increment(-1)}))
        if parent.path in existing:
            group.append(("update", parent, {"replyCount": firestore.Increment(-1)}))
        groups.append(group)
    # Committed before the top-level pass so its "any replies left?" check
    # only sees other people's replies.
    _commit(db, groups)

    existing = _existing_paths(db, [snap.reference.parent.parent for snap in top_level])
    groups = []
    for snap in top_level:
        remaining = snap.reference.parent.where("parentId", "==", snap.id).select([]).limit(1).stream()
        if any(True for _ in remaining):
            groups.append([("update", snap.reference, COMMENT_TOMBSTONE)])
            continue
        post = snap.reference.parent.parent
        groups.extend(_comment_vote_deletes(snap.reference))
        group = [("delete", snap.reference)]
        if post.path in existing:
            group.append(("update", post, {"commentCount": firestore.Increment(-1)}))
        groups.append(group)
    _commit(db, groups)
