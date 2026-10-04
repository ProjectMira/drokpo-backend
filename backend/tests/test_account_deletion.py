"""Account deletion cascade (app/services/account_deletion.py) against an
in-memory Firestore fake. The fake is fuller than the per-test stubs
elsewhere because the cascade spans most collections: each test seeds a
small world, deletes one account, and asserts what's left. Storage and
Firebase Auth are monkeypatched as everywhere else."""

import copy

import pytest
from firebase_admin import firestore
from google.api_core import exceptions as google_exceptions

from app.services import account_deletion
from app.services import communities as communities_service
from app.services import storage as storage_service
from app.services import users as users_service

# --- in-memory Firestore ---------------------------------------------------------


def _parent_path(path: str) -> str:
    return path.rsplit("/", 1)[0]


class FakeSnap:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeDocRef:
    def __init__(self, db, path):
        self._db = db
        self.path = path
        self.id = path.rsplit("/", 1)[1]

    @property
    def parent(self):
        return FakeCollection(self._db, _parent_path(self.path))

    def collection(self, name):
        return FakeCollection(self._db, f"{self.path}/{name}")

    def collections(self):
        prefix = self.path + "/"
        names = sorted({p[len(prefix) :].split("/", 1)[0] for p in self._db.docs if p.startswith(prefix)})
        return [self.collection(name) for name in names]

    def get(self):
        return FakeSnap(self, self._db.docs.get(self.path))


class FakeQuery:
    def __init__(self, db, matches, filters=(), order=None, limit=None):
        self._db = db
        self._matches = matches  # path -> bool: which docs the query ranges over
        self._filters = list(filters)
        self._order = order
        self._limit = limit

    def _clone(self, **changes):
        state = {"filters": self._filters, "order": self._order, "limit": self._limit, **changes}
        return FakeQuery(self._db, self._matches, **state)

    def where(self, field, op, value):
        return self._clone(filters=[*self._filters, (field, op, value)])

    def order_by(self, field, direction=None):
        return self._clone(order=(field, direction))

    def limit(self, n):
        return self._clone(limit=n)

    def select(self, fields):
        return self

    def stream(self):
        results = []
        for path in sorted(self._db.docs):
            data = self._db.docs[path]
            if not self._matches(path) or not all(self._passes(data, f) for f in self._filters):
                continue
            results.append(FakeSnap(FakeDocRef(self._db, path), data))
        if self._order:
            field, direction = self._order
            # Like Firestore, ordering on a field excludes docs that lack it.
            results = [s for s in results if s.to_dict().get(field) is not None]
            results.sort(key=lambda s: s.to_dict()[field], reverse=direction == firestore.Query.DESCENDING)
        return iter(results[: self._limit] if self._limit is not None else results)

    @staticmethod
    def _passes(data, condition):
        field, op, value = condition
        actual = data.get(field)
        if op == "==":
            return actual == value
        if op == "array_contains":
            return value in (actual or [])
        raise NotImplementedError(op)


class FakeCollection(FakeQuery):
    def __init__(self, db, path):
        super().__init__(db, lambda p: _parent_path(p) == path)
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    @property
    def parent(self):
        return FakeDocRef(self._db, _parent_path(self.path)) if "/" in self.path else None

    def document(self, doc_id):
        return FakeDocRef(self._db, f"{self.path}/{doc_id}")

    def recursive(self):
        prefix = self.path + "/"
        return FakeQuery(self._db, lambda p: p.startswith(prefix))


class FakeBatch:
    def __init__(self, db):
        self._db = db
        self._ops = []

    def delete(self, ref):
        self._ops.append(("delete", ref.path, None))

    def update(self, ref, fields):
        self._ops.append(("update", ref.path, fields))

    def commit(self):
        if self._db.fail_on_commit == len(self._db.commits) + 1:
            self._db.fail_on_commit = None
            raise google_exceptions.ServiceUnavailable("injected failure")
        # All-or-nothing, like a real batch: apply to a copy, swap on success.
        docs = copy.deepcopy(self._db.docs)
        for op, path, fields in self._ops:
            if op == "delete":
                docs.pop(path, None)
                continue
            if path not in docs:
                raise google_exceptions.NotFound(f"No document to update: {path}")
            for key, value in fields.items():
                _apply(docs[path], key.split("."), value)
        self._db.docs = docs
        self._db.commits.append(len(self._ops))


def _resolve(value, current):
    if isinstance(value, firestore.Increment):
        return (current or 0) + value.value
    if value is firestore.SERVER_TIMESTAMP:
        return "<server timestamp>"
    if isinstance(value, dict):
        return {k: _resolve(v, None) for k, v in value.items()}
    return value


def _apply(doc, keys, value):
    for key in keys[:-1]:
        doc = doc.setdefault(key, {})
    doc[keys[-1]] = _resolve(value, doc.get(keys[-1]))


class FakeDB:
    def __init__(self, docs):
        self.docs = copy.deepcopy(docs)
        self.commits = []  # writes per committed batch
        self.fail_on_commit = None  # 1-based commit number to fail once

    def collection(self, name):
        return FakeCollection(self, name)

    def collection_group(self, name):
        return FakeQuery(self, lambda p: _parent_path(p).rsplit("/", 1)[-1] == name)

    def get_all(self, refs):
        return [FakeSnap(ref, self.docs.get(ref.path)) for ref in refs]

    def batch(self):
        return FakeBatch(self)

    def paths(self, prefix):
        return sorted(p for p in self.docs if p == prefix or p.startswith(prefix + "/"))


@pytest.fixture
def harness(monkeypatch):
    """Wire a FakeDB into account_deletion and record Storage/Auth calls."""
    calls = {"prefixes": {}, "auth": []}

    def install(docs):
        db = FakeDB(docs)
        monkeypatch.setattr(account_deletion, "get_firestore", lambda: db)
        return db

    monkeypatch.setattr(
        storage_service, "delete_prefix", lambda prefix, keep=frozenset(): calls["prefixes"].update({prefix: set(keep)})
    )
    monkeypatch.setattr(users_service, "ensure_app", lambda: None)
    monkeypatch.setattr(communities_service, "ensure_app", lambda: None)
    monkeypatch.setattr(users_service.firebase_auth, "delete_user", lambda uid: calls["auth"].append(uid))
    calls["install"] = install
    return calls


def _url(path):
    from urllib.parse import quote

    return f"https://firebasestorage.googleapis.com/v0/b/bucket/o/{quote(path, safe='')}?alt=media&token=t"


# --- a person -----------------------------------------------------------------------

# "me" is deleting their account. u2 is a match who reported "me" but whose
# report is closed, u5 is a match with an open report, and u3/u4 are on
# either side of a block. c1 is a community "me" joined.
PERSON_WORLD = {
    "users/me": {"displayName": "Me", "photos": [{"storagePath": "users/me/photos/a.jpg"}]},
    "users/me/swipes/u2": {"action": "like", "fromUid": "me", "toUid": "u2"},
    "users/me/likedNews/n1": {"newsId": "n1"},
    "users/me/likedPosts/poll1": {"postId": "poll1"},
    "users/me/memberships/c1": {"communityName": "C1"},
    "users/u2": {"displayName": "U2"},
    "users/u2/swipes/me": {"action": "like", "fromUid": "u2", "toUid": "me"},
    "users/u2/swipes/u3": {"action": "pass", "fromUid": "u2", "toUid": "u3"},
    "communities/c1": {"name": "C1", "memberCount": 2},
    "communities/c1/members/me": {},
    "communities/c1/members/u2": {},
    "users/u2/memberships/c1": {"communityName": "C1"},
    "blocks/me/blockedUsers/u3": {},
    "blocks/u3/blockedBy/me": {},
    "blocks/me/blockedBy/u4": {},
    "blocks/u4/blockedUsers/me": {},
    "blocks/u3/blockedUsers/u2": {},
    "blocks/u2/blockedBy/u3": {},
    # A poll with comments, and an event.
    "communityPosts/poll1": {
        "communityId": "c1",
        "kind": "poll",
        "poll": {"options": [{"id": "opt1"}, {"id": "opt2"}], "counts": {"opt1": 2, "opt2": 1}},
        "commentCount": 5,
    },
    "communityPosts/poll1/votes/me": {"uid": "me", "optionId": "opt1"},
    "communityPosts/poll1/votes/u2": {"uid": "u2", "optionId": "opt1"},
    "communityPosts/event1": {"communityId": "c1", "kind": "event", "attendeeCount": 2},
    "communityPosts/event1/rsvps/me": {"uid": "me"},
    "communityPosts/event1/rsvps/u2": {"uid": "u2"},
    # My lone comment (someone liked it), my comment with a reply from u2 and
    # one from me, and u2's comment that I disliked.
    "communityPosts/poll1/comments/mine_alone": {"authorUid": "me", "parentId": None, "text": "hi", "likeCount": 1},
    "communityPosts/poll1/comments/mine_alone/votes/u2": {"uid": "u2", "value": "like"},
    "communityPosts/poll1/comments/mine_thread": {
        "authorUid": "me",
        "authorKind": "person",
        "authorName": "Me",
        "authorPhotoUrl": "https://photo",
        "parentId": None,
        "text": None,
        "audioUrl": _url("commentAudio/me/c.m4a"),
        "audioDurationSec": 4,
        "replyCount": 2,
    },
    "communityPosts/poll1/comments/reply_other": {"authorUid": "u2", "parentId": "mine_thread", "text": "yo"},
    "communityPosts/poll1/comments/reply_mine": {"authorUid": "me", "parentId": "mine_thread", "text": "thx"},
    "communityPosts/poll1/comments/other": {"authorUid": "u2", "parentId": None, "text": "hm", "dislikeCount": 1},
    "communityPosts/poll1/comments/other/votes/me": {"uid": "me", "value": "dislike"},
    # A match whose other side's report against me is closed.
    "matches/me_u2": {
        "users": ["me", "u2"],
        "status": "active",
        "lastMessage": {"text": "see you", "senderId": "me"},
    },
    "matches/me_u2/messages/m1": {"senderId": "me", "text": "📷 Photo", "imageUrl": _url("chatMedia/me/a.jpg"), "createdAt": 1},
    "matches/me_u2/messages/m2": {"senderId": "u2", "text": "nice", "createdAt": 2},
    "matches/me_u2/messages/m3": {"senderId": "me", "text": "see you", "createdAt": 3},
    # A match whose other side has an open report against me: evidence hold.
    "matches/me_u5": {"users": ["me", "u5"], "status": "unmatched", "lastMessage": {"text": "stop", "senderId": "u5"}},
    "matches/me_u5/messages/m4": {"senderId": "me", "text": "🎤 Voice message", "audioUrl": _url("chatMedia/me/v.m4a"), "createdAt": 4},
    "matches/me_u5/messages/m5": {"senderId": "u5", "text": "stop", "createdAt": 5},
    "reports/r1": {"reporterUid": "u5", "reportedUid": "me", "status": "open"},
    "reports/r2": {"reporterUid": "u2", "reportedUid": "me", "status": "closed"},
}


def test_delete_account_removes_the_persons_whole_trail(harness):
    db = harness["install"](PERSON_WORLD)

    users_service.delete_account("me")

    docs = db.docs
    # Own doc, subcollections, and every swipe/block/membership marker naming them.
    assert db.paths("users/me") == []
    assert "users/u2/swipes/me" not in docs and "users/u2/swipes/u3" in docs
    assert db.paths("blocks/me") == []
    assert "blocks/u3/blockedBy/me" not in docs and "blocks/u4/blockedUsers/me" not in docs
    assert "blocks/u3/blockedUsers/u2" in docs and "blocks/u2/blockedBy/u3" in docs
    assert "communities/c1/members/me" not in docs and "communities/c1/members/u2" in docs
    assert docs["communities/c1"]["memberCount"] == 1

    # Votes and RSVPs retracted, counters moved; other people's untouched.
    assert docs["communityPosts/poll1"]["poll"]["counts"] == {"opt1": 1, "opt2": 1}
    assert "communityPosts/poll1/votes/u2" in docs
    assert docs["communityPosts/event1"]["attendeeCount"] == 1
    assert "communityPosts/event1/rsvps/u2" in docs
    assert docs["communityPosts/poll1/comments/other"]["dislikeCount"] == 0
    assert "communityPosts/poll1/comments/other/votes/me" not in docs

    # Comments: the lone one and my reply are gone (with others' votes on
    # them); the thread with u2's reply is tombstoned so that reply survives.
    assert db.paths("communityPosts/poll1/comments/mine_alone") == []
    assert "communityPosts/poll1/comments/reply_mine" not in docs
    assert docs["communityPosts/poll1/comments/mine_thread"] == {
        **account_deletion.COMMENT_TOMBSTONE,
        "parentId": None,
        "replyCount": 1,
    }
    assert "communityPosts/poll1/comments/reply_other" in docs
    assert docs["communityPosts/poll1"]["commentCount"] == 3

    # Chat: my messages gone where nobody's report is open, the preview of my
    # message cleared; held for evidence where u5's report is open.
    assert docs["matches/me_u2"]["status"] == "unmatched"
    assert docs["matches/me_u2"]["lastMessage"] is None
    assert db.paths("matches/me_u2") == ["matches/me_u2", "matches/me_u2/messages/m2"]
    assert "matches/me_u5/messages/m4" in docs and "matches/me_u5/messages/m5" in docs
    assert docs["matches/me_u5"]["evidenceHold"] == {"uid": "me", "since": "<server timestamp>"}
    assert docs["matches/me_u5"]["lastMessage"] == {"text": "stop", "senderId": "u5"}
    # Reports themselves are kept.
    assert "reports/r1" in docs and "reports/r2" in docs

    # Storage: whole per-uid folders, minus the held voice note.
    assert harness["prefixes"] == {
        "users/me/": set(),
        "chatMedia/me/": {"chatMedia/me/v.m4a"},
        "commentAudio/me/": set(),
    }
    assert harness["auth"] == ["me"]


def test_delete_account_is_idempotent(harness):
    db = harness["install"](PERSON_WORLD)
    account_deletion.delete_person("me")
    after_first = copy.deepcopy(db.docs)

    account_deletion.delete_person("me")

    assert db.docs == after_first


PERSON_WORLD_COMMITS = 8


@pytest.mark.parametrize("failing_commit", range(1, PERSON_WORLD_COMMITS + 1))
def test_retry_after_a_failed_batch_never_double_counts(harness, failing_commit):
    # Fail each batch of the cascade in turn: the app's retry must land on
    # exactly the state an uninterrupted deletion reaches.
    reference = harness["install"](PERSON_WORLD)
    account_deletion.delete_person("me")
    expected = reference.docs
    assert len(reference.commits) == PERSON_WORLD_COMMITS

    db = harness["install"](PERSON_WORLD)
    db.fail_on_commit = failing_commit
    with pytest.raises(google_exceptions.ServiceUnavailable):
        account_deletion.delete_person("me")
    account_deletion.delete_person("me")  # the app retries

    assert db.docs == expected


def test_votes_on_deleted_targets_are_dropped_without_counter_updates(harness):
    db = harness["install"](
        {
            "users/me": {},
            # Orphans: the post and the comment are already gone.
            "communityPosts/gone/votes/me": {"uid": "me", "optionId": "opt1"},
            "communityPosts/gone/rsvps/me": {"uid": "me"},
            "communityPosts/gone/comments/c/votes/me": {"uid": "me", "value": "like"},
        }
    )

    account_deletion.delete_person("me")

    assert db.docs == {}


def test_reply_on_a_deleted_parent_is_still_removed(harness):
    db = harness["install"](
        {
            "users/me": {},
            "communityPosts/p": {"commentCount": 1},
            "communityPosts/p/comments/r": {"authorUid": "me", "parentId": "gone"},
        }
    )

    account_deletion.delete_person("me")

    assert db.docs == {"communityPosts/p": {"commentCount": 0}}


def test_tombstone_is_exposed_to_clients():
    from app.services import comments as comments_service

    public = comments_service._public_comment("c1", {**account_deletion.COMMENT_TOMBSTONE, "parentId": None})
    assert public["deleted"] is True
    assert public["authorUid"] is None
    assert public["authorName"] == "Deleted account"


# --- a community -----------------------------------------------------------------

COMMUNITY_WORLD = {
    "communities/c1": {"name": "C1", "photos": [{"storagePath": "communities/c1/photos/logo.jpg"}]},
    "communities/c1/members/u1": {},
    "communities/c1/members/u2": {},
    "users/u1": {"displayName": "U1"},
    "users/u1/memberships/c1": {"communityName": "C1"},
    "users/u1/memberships/c2": {"communityName": "C2"},
    "users/u2": {"displayName": "U2"},
    "users/u2/memberships/c1": {"communityName": "C1"},
    # The community's own post, with everything that hangs off it.
    "communityPosts/p1": {"communityId": "c1", "kind": "poll", "commentCount": 1},
    "communityPosts/p1/votes/u1": {"uid": "u1", "optionId": "opt1"},
    "communityPosts/p1/rsvps/u2": {"uid": "u2"},
    "communityPosts/p1/comments/x": {"authorUid": "u1", "parentId": None},
    "communityPosts/p1/comments/x/votes/u2": {"uid": "u2", "value": "like"},
    # Another community's post that c1 commented on and voted in.
    "communities/c2": {"name": "C2", "memberCount": 1},
    "communityPosts/p2": {"communityId": "c2", "kind": "poll", "commentCount": 1, "poll": {"counts": {"opt2": 4}}},
    "communityPosts/p2/comments/y": {"authorUid": "c1", "authorKind": "community", "parentId": None},
    "communityPosts/p2/votes/c1": {"uid": "c1", "optionId": "opt2"},
    # Communities swipe, like and chat as themselves; those docs live under
    # users/c1 although no users/c1 doc exists.
    "users/c1/swipes/u1": {"action": "like", "fromUid": "c1", "toUid": "u1"},
    "users/c1/likedPosts/p2": {"postId": "p2"},
    "users/u1/swipes/c1": {"action": "like", "fromUid": "u1", "toUid": "c1"},
    "matches/c1_u1": {"users": ["c1", "u1"], "status": "active", "lastMessage": {"text": "welcome", "senderId": "c1"}},
    "matches/c1_u1/messages/m1": {"senderId": "c1", "text": "welcome", "createdAt": 1},
    "matches/c1_u1/messages/m2": {"senderId": "u1", "text": "thanks", "createdAt": 2},
}


def test_delete_community_removes_posts_members_and_trail(harness):
    db = harness["install"](COMMUNITY_WORLD)

    communities_service.delete_community("c1")

    docs = db.docs
    assert db.paths("communities/c1") == []
    assert db.paths("communityPosts/p1") == []
    assert db.paths("users/c1") == []
    # Members' mirror docs go; their other memberships and profiles stay.
    assert "users/u1/memberships/c1" not in docs and "users/u2/memberships/c1" not in docs
    assert "users/u1/memberships/c2" in docs and "users/u1" in docs
    assert "users/u1/swipes/c1" not in docs
    # What it did on another community's post.
    assert db.paths("communityPosts/p2") == ["communityPosts/p2"]
    assert docs["communityPosts/p2"]["commentCount"] == 0
    assert docs["communityPosts/p2"]["poll"]["counts"] == {"opt2": 3}
    # Chat, same rules as a person.
    assert docs["matches/c1_u1"]["status"] == "unmatched"
    assert docs["matches/c1_u1"]["lastMessage"] is None
    assert db.paths("matches/c1_u1") == ["matches/c1_u1", "matches/c1_u1/messages/m2"]

    assert harness["prefixes"] == {
        "communities/c1/": set(),
        "chatMedia/c1/": set(),
        "commentAudio/c1/": set(),
    }
    assert harness["auth"] == ["c1"]


# --- batching -----------------------------------------------------------------------


def test_commit_chunks_batches_without_splitting_groups(monkeypatch):
    monkeypatch.setattr(account_deletion, "BATCH_CHUNK_SIZE", 3)
    db = FakeDB({f"d/{i}": {"n": 1} for i in range(6)})
    ref = lambda i: db.collection("d").document(str(i))  # noqa: E731
    groups = [
        [("delete", ref(0)), ("update", ref(1), {"n": firestore.Increment(-1)})],
        [("delete", ref(2)), ("update", ref(3), {"n": firestore.Increment(-1)})],
        [("delete", ref(4))],
        [("delete", ref(5))],
    ]

    account_deletion._commit(db, groups)

    # The second pair would straddle a 3-write batch, so it starts a new one.
    assert db.commits == [2, 3, 1]
    assert db.docs == {"d/1": {"n": 0}, "d/3": {"n": 0}}


def test_commit_with_nothing_to_write_commits_nothing():
    db = FakeDB({})
    account_deletion._commit(db, [])
    assert db.commits == []


# --- storage helpers --------------------------------------------------------------


class StubBlob:
    def __init__(self, name, bucket):
        self.name = name
        self._bucket = bucket

    def delete(self):
        self._bucket.deleted.append(self.name)


class StubBucket:
    def __init__(self, names):
        self.names = names
        self.deleted = []

    def list_blobs(self, prefix):
        return [StubBlob(n, self) for n in self.names if n.startswith(prefix)]


def test_delete_prefix_deletes_folder_except_kept(monkeypatch):
    bucket = StubBucket(["chatMedia/me/a.jpg", "chatMedia/me/v.m4a", "chatMedia/me2/b.jpg"])
    monkeypatch.setattr(storage_service, "get_bucket", lambda: bucket)

    storage_service.delete_prefix("chatMedia/me/", keep={"chatMedia/me/v.m4a"})

    assert bucket.deleted == ["chatMedia/me/a.jpg"]


def test_delete_prefix_requires_trailing_slash(monkeypatch):
    # "users/me" would also sweep another account's "users/me2/...".
    monkeypatch.setattr(storage_service, "get_bucket", lambda: StubBucket(["users/me2/photos/x.jpg"]))
    with pytest.raises(ValueError):
        storage_service.delete_prefix("users/me")


def test_path_from_download_url_inverts_download_urls():
    assert storage_service.path_from_download_url(_url("chatMedia/me/a b.jpg")) == "chatMedia/me/a b.jpg"
    assert storage_service.path_from_download_url("https://example.com/o/chatMedia%2Fme%2Fa.jpg") is None
    assert storage_service.path_from_download_url(None) is None


# --- backfill script ------------------------------------------------------------


def test_backfill_vote_uids_stamps_missing_uid_only(monkeypatch):
    from scripts import backfill_vote_uids

    db = FakeDB(
        {
            "communityPosts/p/votes/u1": {"optionId": "opt1"},
            "communityPosts/p/votes/u2": {"optionId": "opt1", "uid": "u2"},
            "communityPosts/p/comments/c/votes/u3": {"value": "like"},
            "communityPosts/p/rsvps/u4": {},
        }
    )
    monkeypatch.setattr(backfill_vote_uids, "get_firestore", lambda: db)

    backfill_vote_uids.main()

    assert db.docs["communityPosts/p/votes/u1"] == {"optionId": "opt1", "uid": "u1"}
    assert db.docs["communityPosts/p/comments/c/votes/u3"] == {"value": "like", "uid": "u3"}
    assert db.docs["communityPosts/p/rsvps/u4"] == {"uid": "u4"}
    assert db.commits == [2, 1]  # the already-stamped u2 vote isn't rewritten
