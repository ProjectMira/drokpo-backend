from firebase_admin import firestore

from app.firebase import get_firestore
from app.models.community_post import CommentIn
from app.services.communityposts import COMMUNITY_POSTS, PostNotFoundError

COMMENTS = "comments"
COMMENT_VOTES = "votes"
DEFAULT_LIMIT = 30
DEFAULT_REPLY_LIMIT = 20
# Firestore batch writes cap at 500 operations; stay well under it when
# cascading a top-level comment's replies.
BATCH_CHUNK_SIZE = 400

# Fields exposed to clients. authorName/authorPhotoUrl are a snapshot taken at
# creation time (same convention as a post's communityName/communityLogoUrl) —
# an author who later renames themselves doesn't rewrite old comments.
PUBLIC_COMMENT_FIELDS = (
    "authorUid",
    "authorKind",
    "authorName",
    "authorPhotoUrl",
    "text",
    "audioUrl",
    "audioDurationSec",
    "parentId",
    "replyCount",
    "likeCount",
    "dislikeCount",
    "createdAt",
)


class CommentNotFoundError(Exception):
    pass


class NotAllowedError(Exception):
    pass


def _author_snapshot(uid: str) -> dict:
    """Whoever is commenting is already known to be a person or a community
    (require_account_uid) — snapshot the bits a comment row needs to render."""
    from app.services import communities as communities_service
    from app.services import users as users_service

    profile = users_service.get_profile(uid)
    if profile:
        photos = profile.get("photos") or []
        return {
            "authorUid": uid,
            "authorKind": "person",
            "authorName": profile.get("displayName"),
            "authorPhotoUrl": photos[0].get("url") if photos else None,
        }
    community = communities_service.get_community(uid)
    photos = (community or {}).get("photos") or []
    return {
        "authorUid": uid,
        "authorKind": "community",
        "authorName": (community or {}).get("name"),
        "authorPhotoUrl": photos[0].get("url") if photos else None,
    }


def _public_comment(comment_id: str, data: dict, my_vote: str | None = None) -> dict:
    return {
        "commentId": comment_id,
        **{k: data[k] for k in PUBLIC_COMMENT_FIELDS if k in data},
        "myVote": my_vote,
    }


def create_comment(post_id: str, uid: str, payload: CommentIn) -> dict:
    payload.validate_shape()
    db = get_firestore()
    post_ref = db.collection(COMMUNITY_POSTS).document(post_id)
    post_snap = post_ref.get()
    if not post_snap.exists or not post_snap.to_dict().get("active"):
        raise PostNotFoundError("Post not found")

    comments_ref = post_ref.collection(COMMENTS)

    audio_url = None
    if payload.audioStoragePath:
        from app.services import storage as storage_service

        if not payload.audioStoragePath.startswith(f"commentAudio/{uid}/"):
            raise ValueError("audioStoragePath must be one of your own comment recordings")
        audio_url = storage_service.ensure_download_url(payload.audioStoragePath)

    top_level_id = payload.parentId
    if top_level_id:
        parent_snap = comments_ref.document(top_level_id).get()
        if not parent_snap.exists:
            raise CommentNotFoundError("The comment you're replying to no longer exists")
        # Replying to a reply attaches to the same top-level thread, same as
        # Instagram's one-level-deep model.
        top_level_id = parent_snap.to_dict().get("parentId") or top_level_id

    doc = {
        **_author_snapshot(uid),
        "text": payload.text,
        "audioUrl": audio_url,
        "audioDurationSec": payload.audioDurationSec if audio_url else None,
        "parentId": top_level_id,
        "replyCount": 0,
        "likeCount": 0,
        "dislikeCount": 0,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }

    comment_ref = comments_ref.document()
    batch = db.batch()
    batch.set(comment_ref, doc)
    batch.update(post_ref, {"commentCount": firestore.Increment(1)})
    if top_level_id:
        batch.update(comments_ref.document(top_level_id), {"replyCount": firestore.Increment(1)})
    batch.commit()

    # One extra read resolves the SERVER_TIMESTAMP sentinel to a real value so
    # the response carries the same shape (and a real createdAt) as the list
    # endpoints — comments are moderate-traffic, the round trip is cheap.
    created = comment_ref.get().to_dict()
    return _public_comment(comment_ref.id, created)


def list_comments(post_id: str, uid: str, limit: int = DEFAULT_LIMIT, before: str | None = None) -> list[dict]:
    db = get_firestore()
    comments_ref = db.collection(COMMUNITY_POSTS).document(post_id).collection(COMMENTS)
    query = comments_ref.where("parentId", "==", None).order_by(
        "createdAt", direction=firestore.Query.DESCENDING
    )
    if before:
        cursor = comments_ref.document(before).get()
        if cursor.exists:
            query = query.start_after(cursor)
    docs = [(doc.id, doc.to_dict()) for doc in query.limit(limit).stream()]
    return _hydrate_comments(db, post_id, uid, docs)


def list_replies(
    post_id: str, comment_id: str, uid: str, limit: int = DEFAULT_REPLY_LIMIT, before: str | None = None
) -> list[dict]:
    db = get_firestore()
    comments_ref = db.collection(COMMUNITY_POSTS).document(post_id).collection(COMMENTS)
    query = comments_ref.where("parentId", "==", comment_id).order_by(
        "createdAt", direction=firestore.Query.ASCENDING
    )
    if before:
        cursor = comments_ref.document(before).get()
        if cursor.exists:
            query = query.start_after(cursor)
    docs = [(doc.id, doc.to_dict()) for doc in query.limit(limit).stream()]
    return _hydrate_comments(db, post_id, uid, docs)


def _hydrate_comments(db, post_id: str, uid: str, docs: list[tuple[str, dict]]) -> list[dict]:
    """Drop comments from blocked authors and attach the caller's own
    like/dislike vote, same join shape as communityposts._to_public_posts."""
    from app.services import users as users_service

    if not docs:
        return []
    blocked = users_service._blocked_uids(db, uid)
    docs = [(comment_id, data) for comment_id, data in docs if data.get("authorUid") not in blocked]
    if not docs:
        return []

    comments_ref = db.collection(COMMUNITY_POSTS).document(post_id).collection(COMMENTS)
    vote_refs = [comments_ref.document(comment_id).collection(COMMENT_VOTES).document(uid) for comment_id, _ in docs]
    my_votes: dict[str, str] = {}
    for snap in db.get_all(vote_refs):
        if snap.exists:
            # get_all doesn't preserve ref order; recover the comment id from
            # the vote doc's own path instead of zipping against docs.
            comment_id = snap.reference.parent.parent.id
            my_votes[comment_id] = snap.to_dict().get("value")

    return [_public_comment(comment_id, data, my_votes.get(comment_id)) for comment_id, data in docs]


def delete_comment(post_id: str, comment_id: str, uid: str) -> None:
    db = get_firestore()
    post_ref = db.collection(COMMUNITY_POSTS).document(post_id)
    post_snap = post_ref.get()
    if not post_snap.exists:
        raise PostNotFoundError("Post not found")
    post = post_snap.to_dict()

    comments_ref = post_ref.collection(COMMENTS)
    comment_ref = comments_ref.document(comment_id)
    comment_snap = comment_ref.get()
    if not comment_snap.exists:
        raise CommentNotFoundError("Comment not found")
    comment = comment_snap.to_dict()

    if uid != comment.get("authorUid") and uid != post.get("communityId"):
        raise NotAllowedError("Only the comment's author or the post's community can delete it")

    parent_id = comment.get("parentId")
    deleted_replies = 0
    if parent_id is None:
        # Deleting a top-level comment cascades its replies (Instagram
        # behavior) — vote subcollections under the deleted docs are left as
        # harmless orphans, same tradeoff as post/community deletion elsewhere.
        replies = list(comments_ref.where("parentId", "==", comment_id).stream())
        deleted_replies = len(replies)
        for i in range(0, len(replies), BATCH_CHUNK_SIZE):
            batch = db.batch()
            for reply in replies[i : i + BATCH_CHUNK_SIZE]:
                batch.delete(reply.reference)
            batch.commit()

    comment_ref.delete()
    post_ref.update({"commentCount": firestore.Increment(-(1 + deleted_replies))})
    if parent_id is not None:
        comments_ref.document(parent_id).update({"replyCount": firestore.Increment(-1)})


@firestore.transactional
def _vote_comment_transaction(transaction, db, post_id: str, comment_id: str, uid: str, value: str | None) -> dict:
    comment_ref = db.collection(COMMUNITY_POSTS).document(post_id).collection(COMMENTS).document(comment_id)
    vote_ref = comment_ref.collection(COMMENT_VOTES).document(uid)

    # Both reads before any write — Firestore transactions require it.
    comment_snap = comment_ref.get(transaction=transaction)
    vote_snap = vote_ref.get(transaction=transaction)

    if not comment_snap.exists:
        raise CommentNotFoundError("Comment not found")
    comment = comment_snap.to_dict()

    previous = vote_snap.to_dict().get("value") if vote_snap.exists else None
    like_count = comment.get("likeCount", 0)
    dislike_count = comment.get("dislikeCount", 0)

    if previous == value:
        return {"likeCount": like_count, "dislikeCount": dislike_count, "myVote": value}

    if previous == "like":
        like_count = max(0, like_count - 1)
    elif previous == "dislike":
        dislike_count = max(0, dislike_count - 1)
    if value == "like":
        like_count += 1
    elif value == "dislike":
        dislike_count += 1

    if value is None:
        transaction.delete(vote_ref)
    else:
        transaction.set(
            vote_ref,
            {
                "value": value,
                "createdAt": vote_snap.get("createdAt") if vote_snap.exists else firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
        )
    transaction.update(comment_ref, {"likeCount": like_count, "dislikeCount": dislike_count})
    return {"likeCount": like_count, "dislikeCount": dislike_count, "myVote": value}


def vote_comment(post_id: str, comment_id: str, uid: str, value: str | None) -> dict:
    db = get_firestore()
    transaction = db.transaction()
    return _vote_comment_transaction(transaction, db, post_id, comment_id, uid, value)
