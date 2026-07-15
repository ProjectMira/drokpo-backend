"""Comments on community posts: model validation, service logic (stub
Firestore, same conventions as test_community_posts_service.py /
test_events_rsvp.py), and router-level RBAC + status-code mapping (service
mocked wholesale, same convention as test_community_posts.py)."""

from firebase_admin import firestore

from app.models.community_post import CommentIn
from app.services import comments as comments_service
from conftest import TEST_UID

# ---------------------------------------------------------------------------
# CommentIn.validate_shape() — pure model logic
# ---------------------------------------------------------------------------


def test_comment_requires_exactly_one_of_text_or_audio():
    import pytest

    with pytest.raises(ValueError):
        CommentIn().validate_shape()
    with pytest.raises(ValueError):
        CommentIn(text="hi", audioStoragePath="commentAudio/u1/a.m4a", audioDurationSec=5).validate_shape()
    CommentIn(text="hi").validate_shape()
    CommentIn(audioStoragePath="commentAudio/u1/a.m4a", audioDurationSec=5).validate_shape()


def test_comment_audio_requires_duration():
    import pytest

    with pytest.raises(ValueError):
        CommentIn(audioStoragePath="commentAudio/u1/a.m4a").validate_shape()


def test_comment_text_len_capped():
    import pytest

    with pytest.raises(ValueError):
        CommentIn(text="x" * 2201)
    CommentIn(text="x" * 2200)


def test_comment_audio_duration_bounds():
    import pytest

    with pytest.raises(ValueError):
        CommentIn(audioDurationSec=0)
    with pytest.raises(ValueError):
        CommentIn(audioDurationSec=61)
    CommentIn(audioDurationSec=60)


def test_comment_blank_text_becomes_none():
    assert CommentIn(text="   ").text is None


# ---------------------------------------------------------------------------
# create_comment() — stub Firestore, real branching logic
# ---------------------------------------------------------------------------


class _Snap:
    def __init__(self, id_, data, exists):
        self.id = id_
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


class _Batch:
    def __init__(self):
        self.sets = []
        self.updates = []

    def set(self, ref, data):
        self.sets.append((ref, data))

    def update(self, ref, data):
        self.updates.append((ref, data))

    def commit(self):
        for ref, data in self.sets:
            ref._committed = {**data, "createdAt": "2026-07-16T00:00:00+00:00"}


class _CommentRef:
    def __init__(self, id_, existing=None):
        self.id = id_
        self._existing = existing
        self._committed = None

    def get(self):
        if self._committed is not None:
            return _Snap(self.id, self._committed, True)
        return _Snap(self.id, self._existing, self._existing is not None)

    def collection(self, name):
        assert name == comments_service.COMMENT_VOTES
        return _VotesCollection({})


class _VotesCollection:
    def __init__(self, votes):
        self._votes = votes

    def document(self, uid):
        return _CommentRef(uid, existing=self._votes.get(uid))


class _CommentsCollection:
    def __init__(self, existing=None):
        self._existing = existing or {}
        self.created = []

    def document(self, doc_id=None):
        if doc_id is None:
            ref = _CommentRef(f"new-comment-{len(self.created)}")
            self.created.append(ref)
            return ref
        return _CommentRef(doc_id, existing=self._existing.get(doc_id))

    def where(self, field, op, value):
        matches = [
            (doc_id, data) for doc_id, data in self._existing.items() if data.get(field) == value
        ]
        return _Query(matches)

    def order_by(self, field, direction=None):
        return _Query(list(self._existing.items()))


class _Query:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def start_after(self, cursor):
        return self

    def stream(self):
        return iter(_QueryDoc(doc_id, data) for doc_id, data in self._docs)


class _QueryDoc:
    def __init__(self, id_, data):
        self.id = id_
        self._data = data
        self.reference = _StreamRef(id_)

    def to_dict(self):
        return self._data


class _StreamRef:
    def __init__(self, id_):
        self.id = id_


class _PostRef:
    def __init__(self, post_data, comments_collection):
        self._post_data = post_data
        self._comments = comments_collection

    def get(self):
        return _Snap("post-1", self._post_data, self._post_data is not None)

    def collection(self, name):
        assert name == comments_service.COMMENTS
        return self._comments


class _DB:
    def __init__(self, post_ref):
        self._post_ref = post_ref
        self.batch_obj = None

    def collection(self, name):
        assert name == comments_service.COMMUNITY_POSTS
        return self

    def document(self, post_id):
        return self._post_ref

    def batch(self):
        self.batch_obj = _Batch()
        return self.batch_obj

    def get_all(self, refs):
        return [ref.get() for ref in refs]


def _as_person(monkeypatch):
    monkeypatch.setattr(
        "app.services.users.get_profile",
        lambda uid: {"displayName": "Dolma", "photos": [{"url": "https://cdn.example/dolma.jpg"}]},
    )


def test_create_comment_text(monkeypatch):
    comments = _CommentsCollection()
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    result = comments_service.create_comment("post-1", "u1", CommentIn(text="Tashi delek!"))

    assert result["text"] == "Tashi delek!"
    assert result["authorKind"] == "person"
    assert result["authorName"] == "Dolma"
    assert result["parentId"] is None
    assert result["myVote"] is None
    # commentCount incremented on the post, no parent reply-count touch.
    assert (post_ref, {"commentCount": firestore.Increment(1)}) in db.batch_obj.updates


def test_create_comment_rejects_inactive_post(monkeypatch):
    import pytest

    comments = _CommentsCollection()
    post_ref = _PostRef({"active": False}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    with pytest.raises(comments_service.PostNotFoundError):
        comments_service.create_comment("post-1", "u1", CommentIn(text="hi"))


def test_create_comment_rejects_missing_post(monkeypatch):
    import pytest

    db = _DB(_PostRef(None, _CommentsCollection()))
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    with pytest.raises(comments_service.PostNotFoundError):
        comments_service.create_comment("post-1", "u1", CommentIn(text="hi"))


def test_create_comment_reply_increments_parent_reply_count(monkeypatch):
    comments = _CommentsCollection(existing={"top1": {"parentId": None, "authorUid": "u2"}})
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    result = comments_service.create_comment(
        "post-1", "u1", CommentIn(text="reply text", parentId="top1")
    )

    assert result["parentId"] == "top1"
    # The post-level commentCount update has no .id; only compare the named
    # comment-doc updates (each _CommentRef.document(id) call is a fresh
    # instance, so match by id, not identity).
    updates_by_id = {ref.id: data for ref, data in db.batch_obj.updates if hasattr(ref, "id")}
    assert updates_by_id["top1"] == {"replyCount": firestore.Increment(1)}


def test_create_comment_reply_to_reply_coerces_to_top_level(monkeypatch):
    comments = _CommentsCollection(
        existing={
            "top1": {"parentId": None, "authorUid": "u2"},
            "reply1": {"parentId": "top1", "authorUid": "u3"},
        }
    )
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    result = comments_service.create_comment(
        "post-1", "u1", CommentIn(text="reply to a reply", parentId="reply1")
    )

    assert result["parentId"] == "top1"


def test_create_comment_rejects_missing_parent(monkeypatch):
    import pytest

    comments = _CommentsCollection()
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    with pytest.raises(comments_service.CommentNotFoundError):
        comments_service.create_comment("post-1", "u1", CommentIn(text="hi", parentId="gone"))


def test_create_comment_author_snapshot_for_community(monkeypatch):
    comments = _CommentsCollection()
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr(
        "app.services.communities.get_community",
        lambda uid: {"name": "TANY", "photos": [{"url": "https://cdn.example/logo.jpg"}]},
    )

    result = comments_service.create_comment("post-1", "c1", CommentIn(text="hi from us"))

    assert result["authorKind"] == "community"
    assert result["authorName"] == "TANY"
    assert result["authorPhotoUrl"] == "https://cdn.example/logo.jpg"


def test_create_comment_audio_validates_ownership(monkeypatch):
    import pytest

    comments = _CommentsCollection()
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    _as_person(monkeypatch)

    payload = CommentIn(audioStoragePath="commentAudio/someone-else/a.m4a", audioDurationSec=10)
    with pytest.raises(ValueError):
        comments_service.create_comment("post-1", "u1", payload)


def test_create_comment_audio_resolves_download_url(monkeypatch):
    comments = _CommentsCollection()
    post_ref = _PostRef({"active": True}, comments)
    db = _DB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )
    _as_person(monkeypatch)

    payload = CommentIn(audioStoragePath="commentAudio/u1/a.m4a", audioDurationSec=12)
    result = comments_service.create_comment("post-1", "u1", payload)

    assert result["audioUrl"] == "https://cdn.example/commentAudio/u1/a.m4a"
    assert result["audioDurationSec"] == 12
    assert result["text"] is None


# ---------------------------------------------------------------------------
# list_comments() / list_replies() — blocked filter + myVote hydration
# ---------------------------------------------------------------------------


class _ListVoteRef:
    def __init__(self, comment_id, uid):
        self.comment_id = comment_id
        self.id = uid


class _ListVotesCollection:
    def __init__(self, comment_id):
        self.comment_id = comment_id

    def document(self, uid):
        return _ListVoteRef(self.comment_id, uid)


class _ListCommentRef:
    def __init__(self, comment_id, existing=None):
        self.comment_id = comment_id
        self._existing = existing

    def get(self):
        return _Snap(self.comment_id, self._existing, self._existing is not None)

    def collection(self, name):
        assert name == comments_service.COMMENT_VOTES
        return _ListVotesCollection(self.comment_id)


class _ListCommentsCollection:
    def __init__(self, docs):
        self._docs = docs  # comment_id -> data

    def document(self, comment_id):
        return _ListCommentRef(comment_id, existing=self._docs.get(comment_id))

    def where(self, field, op, value):
        matches = [(cid, data) for cid, data in self._docs.items() if data.get(field) == value]
        return _Query(matches)


class _ListPostRef:
    def __init__(self, comments_collection):
        self._comments = comments_collection

    def collection(self, name):
        assert name == comments_service.COMMENTS
        return self._comments


class _VoteReferenceDocParent:
    def __init__(self, comment_id):
        self.id = comment_id


class _VoteReferenceCollectionParent:
    def __init__(self, comment_id):
        self.parent = _VoteReferenceDocParent(comment_id)


class _FakeVoteReference:
    """Just enough of a DocumentReference for _hydrate_comments'
    snap.reference.parent.parent.id to recover the owning comment's id."""

    def __init__(self, comment_id):
        self.parent = _VoteReferenceCollectionParent(comment_id)


class _ListDB:
    def __init__(self, comments_docs, votes_by_comment=None):
        self._comments = _ListCommentsCollection(comments_docs)
        self._votes_by_comment = votes_by_comment or {}

    def collection(self, name):
        assert name == comments_service.COMMUNITY_POSTS
        return self

    def document(self, post_id):
        return _ListPostRef(self._comments)

    def get_all(self, refs):
        snaps = []
        for ref in refs:
            value = self._votes_by_comment.get(ref.comment_id)
            snap = _Snap(ref.id, {"value": value} if value else None, value is not None)
            snap.reference = _FakeVoteReference(ref.comment_id)
            snaps.append(snap)
        return snaps


def test_list_comments_attaches_my_vote_and_filters_blocked(monkeypatch):
    docs = {
        "c1": {"authorUid": "u2", "parentId": None, "text": "hi"},
        "c2": {"authorUid": "blocked-uid", "parentId": None, "text": "spam"},
    }
    db = _ListDB(docs, votes_by_comment={"c1": "like"})
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    monkeypatch.setattr("app.services.users._blocked_uids", lambda db, uid: {"blocked-uid"})

    result = comments_service.list_comments("post-1", TEST_UID)

    assert [c["commentId"] for c in result] == ["c1"]
    assert result[0]["myVote"] == "like"


def test_list_replies_returns_thread_for_parent(monkeypatch):
    docs = {
        "top1": {"authorUid": "u2", "parentId": None, "text": "top"},
        "r1": {"authorUid": "u3", "parentId": "top1", "text": "reply 1"},
        "r2": {"authorUid": "u4", "parentId": "top1", "text": "reply 2"},
    }
    db = _ListDB(docs)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    monkeypatch.setattr("app.services.users._blocked_uids", lambda db, uid: set())

    result = comments_service.list_replies("post-1", "top1", TEST_UID)

    assert {c["commentId"] for c in result} == {"r1", "r2"}


def test_list_comments_empty_skips_vote_lookup(monkeypatch):
    db = _ListDB({})
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    def explode(refs):
        raise AssertionError("get_all must not be called for an empty page")

    monkeypatch.setattr(db, "get_all", explode)
    assert comments_service.list_comments("post-1", TEST_UID) == []


def test_list_comments_before_cursor_looks_up_the_named_comment(monkeypatch):
    docs = {"c1": {"authorUid": "u2", "parentId": None, "text": "hi"}}
    db = _ListDB(docs)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)
    monkeypatch.setattr("app.services.users._blocked_uids", lambda db, uid: set())

    # A `before` id that doesn't exist in the page is simply ignored (no
    # start_after applied) rather than raising — mirrors list_posts' cursor.
    result = comments_service.list_comments("post-1", TEST_UID, before="c1")
    assert [c["commentId"] for c in result] == ["c1"]


# ---------------------------------------------------------------------------
# delete_comment() — author/community permission + reply cascade
# ---------------------------------------------------------------------------


class _DeleteCommentRef:
    def __init__(self, id_, data):
        self.id = id_
        self._data = data
        self.deleted = False
        self.updates = []

    def get(self):
        return _Snap(self.id, self._data, self._data is not None)

    def delete(self):
        self.deleted = True

    def update(self, data):
        self.updates.append(data)


class _DeleteCommentsCollection:
    def __init__(self, docs):
        self._refs = {doc_id: _DeleteCommentRef(doc_id, data) for doc_id, data in docs.items()}

    def document(self, doc_id):
        return self._refs.setdefault(doc_id, _DeleteCommentRef(doc_id, None))

    def where(self, field, op, value):
        matches = [ref for ref in self._refs.values() if (ref._data or {}).get(field) == value]
        return _StreamOnly(matches)


class _StreamOnly:
    def __init__(self, refs):
        self._refs = refs

    def stream(self):
        return iter(_ReplyDoc(ref) for ref in self._refs)


class _ReplyDoc:
    def __init__(self, ref):
        self.reference = ref


class _DeletePostRef:
    def __init__(self, post_data, comments):
        self._data = post_data
        self._comments = comments
        self.updates = []

    def get(self):
        return _Snap("post-1", self._data, self._data is not None)

    def collection(self, name):
        return self._comments

    def update(self, data):
        self.updates.append(data)


class _DeleteBatch:
    def __init__(self):
        self.deletes = []

    def delete(self, ref):
        self.deletes.append(ref)
        ref.deleted = True

    def commit(self):
        pass


class _DeleteDB:
    def __init__(self, post_ref):
        self._post_ref = post_ref

    def collection(self, name):
        return self

    def document(self, post_id):
        return self._post_ref

    def batch(self):
        return _DeleteBatch()


def test_delete_comment_by_author(monkeypatch):
    comments = _DeleteCommentsCollection({"c1": {"authorUid": "u1", "parentId": None}})
    post_ref = _DeletePostRef({"communityId": "cid1"}, comments)
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    comments_service.delete_comment("post-1", "c1", "u1")

    assert comments.document("c1").deleted
    assert post_ref.updates == [{"commentCount": firestore.Increment(-1)}]


def test_delete_comment_by_owning_community(monkeypatch):
    comments = _DeleteCommentsCollection({"c1": {"authorUid": "someone-else", "parentId": None}})
    post_ref = _DeletePostRef({"communityId": "cid1"}, comments)
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    comments_service.delete_comment("post-1", "c1", "cid1")

    assert comments.document("c1").deleted


def test_delete_comment_rejects_stranger(monkeypatch):
    import pytest

    comments = _DeleteCommentsCollection({"c1": {"authorUid": "someone-else", "parentId": None}})
    post_ref = _DeletePostRef({"communityId": "cid1"}, comments)
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    with pytest.raises(comments_service.NotAllowedError):
        comments_service.delete_comment("post-1", "c1", "random-uid")
    assert not comments.document("c1").deleted


def test_delete_top_level_comment_cascades_replies(monkeypatch):
    comments = _DeleteCommentsCollection(
        {
            "top1": {"authorUid": "u1", "parentId": None},
            "r1": {"authorUid": "u2", "parentId": "top1"},
            "r2": {"authorUid": "u3", "parentId": "top1"},
        }
    )
    post_ref = _DeletePostRef({"communityId": "cid1"}, comments)
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    comments_service.delete_comment("post-1", "top1", "u1")

    assert comments.document("top1").deleted
    assert comments.document("r1").deleted
    assert comments.document("r2").deleted
    assert post_ref.updates == [{"commentCount": firestore.Increment(-3)}]


def test_delete_reply_decrements_parent_reply_count(monkeypatch):
    comments = _DeleteCommentsCollection(
        {
            "top1": {"authorUid": "u1", "parentId": None},
            "r1": {"authorUid": "u2", "parentId": "top1"},
        }
    )
    post_ref = _DeletePostRef({"communityId": "cid1"}, comments)
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    comments_service.delete_comment("post-1", "r1", "u2")

    assert comments.document("r1").deleted
    assert not comments.document("top1").deleted
    assert comments.document("top1").updates == [{"replyCount": firestore.Increment(-1)}]
    assert post_ref.updates == [{"commentCount": firestore.Increment(-1)}]


def test_delete_comment_rejects_missing_post(monkeypatch):
    import pytest

    post_ref = _DeletePostRef(None, _DeleteCommentsCollection({}))
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    with pytest.raises(comments_service.PostNotFoundError):
        comments_service.delete_comment("post-1", "c1", "u1")


def test_delete_comment_rejects_missing_comment(monkeypatch):
    import pytest

    post_ref = _DeletePostRef({"communityId": "cid1"}, _DeleteCommentsCollection({}))
    db = _DeleteDB(post_ref)
    monkeypatch.setattr(comments_service, "get_firestore", lambda: db)

    with pytest.raises(comments_service.CommentNotFoundError):
        comments_service.delete_comment("post-1", "gone", "u1")


# ---------------------------------------------------------------------------
# _vote_comment_transaction — real like/dislike count math, stubbed Firestore
# (same .to_wrap technique as test_events_rsvp.py's RSVP transaction tests)
# ---------------------------------------------------------------------------


class _VoteSnap:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data

    def get(self, field):
        return (self._data or {}).get(field)


class _VoteRef:
    def __init__(self, snap):
        self._snap = snap

    def get(self, transaction=None):
        return self._snap


class _VoteVotesCollection:
    def __init__(self, ref):
        self._ref = ref

    def document(self, uid):
        return self._ref


class _VoteCommentRef:
    def __init__(self, comment_snap, vote_ref):
        self._comment_snap = comment_snap
        self._vote_ref = vote_ref

    def get(self, transaction=None):
        return self._comment_snap

    def collection(self, name):
        assert name == comments_service.COMMENT_VOTES
        return _VoteVotesCollection(self._vote_ref)


class _VoteCommentsCollection:
    def __init__(self, comment_ref):
        self._comment_ref = comment_ref

    def document(self, comment_id):
        return self._comment_ref


class _VotePostRef:
    def __init__(self, comment_ref):
        self._comments = _VoteCommentsCollection(comment_ref)

    def collection(self, name):
        assert name == comments_service.COMMENTS
        return self._comments


class _VoteDB:
    def __init__(self, post_ref):
        self._post_ref = post_ref

    def collection(self, name):
        assert name == comments_service.COMMUNITY_POSTS
        return self

    def document(self, post_id):
        return self._post_ref


class _FakeTransaction:
    def __init__(self):
        self.sets = []
        self.deletes = []
        self.updates = []

    def set(self, ref, data):
        self.sets.append((ref, data))

    def delete(self, ref):
        self.deletes.append(ref)

    def update(self, ref, data):
        self.updates.append((ref, data))


def _run_vote(comment_data, existing_vote, value):
    comment_snap = _VoteSnap(comment_data, exists=comment_data is not None)
    vote_snap = _VoteSnap({"value": existing_vote} if existing_vote else None, exists=existing_vote is not None)
    vote_ref = _VoteRef(vote_snap)
    comment_ref = _VoteCommentRef(comment_snap, vote_ref)
    post_ref = _VotePostRef(comment_ref)
    db = _VoteDB(post_ref)
    transaction = _FakeTransaction()
    raw = comments_service._vote_comment_transaction.to_wrap
    result = raw(transaction, db, "post-1", "c1", TEST_UID, value)
    return result, transaction


def test_vote_none_to_like():
    result, txn = _run_vote({"likeCount": 0, "dislikeCount": 0}, existing_vote=None, value="like")
    assert result == {"likeCount": 1, "dislikeCount": 0, "myVote": "like"}
    assert len(txn.sets) == 1
    assert txn.updates == [(txn.updates[0][0], {"likeCount": 1, "dislikeCount": 0})]


def test_vote_like_to_dislike_moves_both_counters():
    result, txn = _run_vote({"likeCount": 1, "dislikeCount": 0}, existing_vote="like", value="dislike")
    assert result == {"likeCount": 0, "dislikeCount": 1, "myVote": "dislike"}


def test_vote_dislike_to_none_clears():
    result, txn = _run_vote({"likeCount": 0, "dislikeCount": 1}, existing_vote="dislike", value=None)
    assert result == {"likeCount": 0, "dislikeCount": 0, "myVote": None}
    assert len(txn.deletes) == 1
    assert txn.sets == []


def test_vote_idempotent_relike_is_noop():
    result, txn = _run_vote({"likeCount": 3, "dislikeCount": 0}, existing_vote="like", value="like")
    assert result == {"likeCount": 3, "dislikeCount": 0, "myVote": "like"}
    assert txn.sets == [] and txn.deletes == [] and txn.updates == []


def test_vote_counts_never_go_negative():
    result, _ = _run_vote({"likeCount": 0, "dislikeCount": 0}, existing_vote="like", value=None)
    assert result["likeCount"] == 0


def test_vote_rejects_missing_comment():
    import pytest

    with pytest.raises(comments_service.CommentNotFoundError):
        _run_vote(None, existing_vote=None, value="like")


# ---------------------------------------------------------------------------
# Router: RBAC + status-code mapping (service mocked wholesale)
# ---------------------------------------------------------------------------


def test_create_comment_endpoint(client, monkeypatch):
    snapshot = {"commentId": "c1", "text": "hi", "authorKind": "person", "myVote": None}
    monkeypatch.setattr("app.services.comments.create_comment", lambda post_id, uid, payload: snapshot)
    response = client.post("/api/posts/post-1/comments", json={"text": "hi"})
    assert response.status_code == 200
    assert response.json() == snapshot


def test_create_comment_endpoint_allows_community(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr(
        "app.services.comments.create_comment", lambda post_id, uid, payload: {"commentId": "c1"}
    )
    response = client.post("/api/posts/post-1/comments", json={"text": "hi from a community"})
    assert response.status_code == 200


def test_create_comment_endpoint_rejects_neither_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    response = client.post("/api/posts/post-1/comments", json={"text": "hi"})
    assert response.status_code == 403


def test_create_comment_endpoint_rejects_shape_violation(client):
    response = client.post("/api/posts/post-1/comments", json={})
    assert response.status_code == 400


def test_create_comment_endpoint_missing_post(client, monkeypatch):
    def fail(post_id, uid, payload):
        raise comments_service.PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.comments.create_comment", fail)
    response = client.post("/api/posts/nope/comments", json={"text": "hi"})
    assert response.status_code == 404


def test_create_comment_endpoint_missing_parent(client, monkeypatch):
    def fail(post_id, uid, payload):
        raise comments_service.CommentNotFoundError("Comment not found")

    monkeypatch.setattr("app.services.comments.create_comment", fail)
    response = client.post("/api/posts/post-1/comments", json={"text": "hi", "parentId": "gone"})
    assert response.status_code == 404


def test_list_comments_endpoint(client, monkeypatch):
    comments = [{"commentId": "c1", "text": "hi"}]
    captured = {}
    monkeypatch.setattr(
        "app.services.comments.list_comments",
        lambda post_id, uid, limit, before: captured.update(post_id=post_id, limit=limit, before=before)
        or comments,
    )
    response = client.get("/api/posts/post-1/comments", params={"limit": 10, "before": "c0"})
    assert response.status_code == 200
    assert response.json() == {"comments": comments}
    assert captured == {"post_id": "post-1", "limit": 10, "before": "c0"}


def test_list_replies_endpoint(client, monkeypatch):
    replies = [{"commentId": "r1", "text": "reply"}]
    monkeypatch.setattr(
        "app.services.comments.list_replies", lambda post_id, comment_id, uid, limit, before: replies
    )
    response = client.get("/api/posts/post-1/comments/top1/replies")
    assert response.status_code == 200
    assert response.json() == {"replies": replies}


def test_delete_comment_endpoint(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.comments.delete_comment",
        lambda post_id, comment_id, uid: captured.update(post_id=post_id, comment_id=comment_id, uid=uid),
    )
    response = client.delete("/api/posts/post-1/comments/c1")
    assert response.status_code == 200
    assert captured == {"post_id": "post-1", "comment_id": "c1", "uid": TEST_UID}


def test_delete_comment_endpoint_forbidden(client, monkeypatch):
    def fail(post_id, comment_id, uid):
        raise comments_service.NotAllowedError("Only the comment's author or the post's community can delete it")

    monkeypatch.setattr("app.services.comments.delete_comment", fail)
    response = client.delete("/api/posts/post-1/comments/c1")
    assert response.status_code == 403


def test_delete_comment_endpoint_not_found(client, monkeypatch):
    def fail(post_id, comment_id, uid):
        raise comments_service.CommentNotFoundError("Comment not found")

    monkeypatch.setattr("app.services.comments.delete_comment", fail)
    response = client.delete("/api/posts/post-1/comments/gone")
    assert response.status_code == 404


def test_vote_comment_endpoint(client, monkeypatch):
    result = {"likeCount": 1, "dislikeCount": 0, "myVote": "like"}
    captured = {}
    monkeypatch.setattr(
        "app.services.comments.vote_comment",
        lambda post_id, comment_id, uid, value: captured.update(value=value) or result,
    )
    response = client.put("/api/posts/post-1/comments/c1/vote", json={"value": "like"})
    assert response.status_code == 200
    assert response.json() == result
    assert captured["value"] == "like"


def test_vote_comment_endpoint_rejects_invalid_value(client):
    response = client.put("/api/posts/post-1/comments/c1/vote", json={"value": "love"})
    assert response.status_code == 422


def test_clear_comment_vote_endpoint(client, monkeypatch):
    result = {"likeCount": 0, "dislikeCount": 0, "myVote": None}
    captured = {}
    monkeypatch.setattr(
        "app.services.comments.vote_comment",
        lambda post_id, comment_id, uid, value: captured.update(value=value) or result,
    )
    response = client.delete("/api/posts/post-1/comments/c1/vote")
    assert response.status_code == 200
    assert captured["value"] is None
