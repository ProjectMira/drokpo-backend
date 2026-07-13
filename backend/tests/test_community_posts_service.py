"""Unit tests for the community-post logic that isn't just Firestore
plumbing: per-kind validation, poll option-id assignment, and photo/logo
resolution — same spirit as test_users_service.py's stub-based tests."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.community_post import CommunityPostIn
from app.services import communities as communities_service
from app.services import communityposts as communityposts_service


def _future_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- CommunityPostIn.validate_kind_shape() — pure model logic ---------------


def test_link_post_requires_url():
    with pytest.raises(ValueError):
        CommunityPostIn(kind="link", title="x").validate_kind_shape()
    CommunityPostIn(kind="link", title="x", linkUrl="https://example.com").validate_kind_shape()


def test_poll_requires_two_to_four_unique_options():
    with pytest.raises(ValueError):
        CommunityPostIn(kind="poll", title="x", pollOptions=["only one"]).validate_kind_shape()
    with pytest.raises(ValueError):
        CommunityPostIn(kind="poll", title="x", pollOptions=["a", "a"]).validate_kind_shape()
    with pytest.raises(ValueError):
        CommunityPostIn(
            kind="poll", title="x", pollOptions=["a", "b", "c", "d", "e"]
        ).validate_kind_shape()
    CommunityPostIn(kind="poll", title="x", pollOptions=["Yes", "No"]).validate_kind_shape()


def test_announcement_has_no_extra_requirements():
    CommunityPostIn(kind="announcement", title="Losar celebration").validate_kind_shape()


def test_event_requires_event_at():
    with pytest.raises(ValueError):
        CommunityPostIn(kind="event", title="Protest").validate_kind_shape()


def test_event_rejects_naive_datetime():
    with pytest.raises(ValueError):
        CommunityPostIn(
            kind="event", title="Protest", eventAt="2099-08-15T18:00:00"
        ).validate_kind_shape()


def test_event_rejects_past_date():
    with pytest.raises(ValueError):
        CommunityPostIn(kind="event", title="Protest", eventAt=_past_iso()).validate_kind_shape()


def test_event_accepts_future_date_and_location():
    CommunityPostIn(
        kind="event", title="Protest", eventAt=_future_iso(), location="City Hall steps"
    ).validate_kind_shape()


def test_event_location_length_capped():
    with pytest.raises(ValueError):
        CommunityPostIn(kind="event", title="x", eventAt=_future_iso(), location="x" * 201)


# --- create_post() — stub Firestore, real branching logic ------------------


class StubRef:
    def __init__(self, id_):
        self.id = id_


class StubPostsCollection:
    def __init__(self):
        self.added = None

    def add(self, doc):
        self.added = doc
        return (None, StubRef("new-post-id"))


class StubDB:
    def __init__(self, collection):
        self._collection = collection

    def collection(self, name):
        return self._collection


def _verified_community(**overrides):
    community = {"uid": "cid1", "name": "TANY", "verification": "verified", "photos": []}
    community.update(overrides)
    return community


def test_create_post_writes_announcement(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: _verified_community())
    posts = StubPostsCollection()
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubDB(posts))

    post_id = communityposts_service.create_post(
        "cid1", CommunityPostIn(kind="announcement", title="Losar", body="Join us")
    )

    assert post_id == "new-post-id"
    assert posts.added["communityId"] == "cid1"
    assert posts.added["communityName"] == "TANY"
    assert posts.added["poll"] is None
    assert posts.added["active"] is True


def test_create_post_poll_assigns_sequential_option_ids(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: _verified_community())
    posts = StubPostsCollection()
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubDB(posts))

    payload = CommunityPostIn(kind="poll", title="Favorite tea?", pollOptions=["Chai", "Butter tea"])
    communityposts_service.create_post("cid1", payload)

    poll = posts.added["poll"]
    assert poll["options"] == [{"id": "opt1", "label": "Chai"}, {"id": "opt2", "label": "Butter tea"}]
    assert poll["counts"] == {"opt1": 0, "opt2": 0}


def test_create_post_writes_event_fields(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: _verified_community())
    posts = StubPostsCollection()
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubDB(posts))

    event_at = _future_iso()
    payload = CommunityPostIn(
        kind="event", title="Rally", eventAt=event_at, location="City Hall steps"
    )
    communityposts_service.create_post("cid1", payload)

    assert posts.added["eventAt"] == event_at
    assert posts.added["location"] == "City Hall steps"
    assert posts.added["attendeeCount"] == 0
    assert posts.added["poll"] is None


def test_create_post_rejects_unverified_community(monkeypatch):
    monkeypatch.setattr(
        communities_service, "get_community", lambda cid: _verified_community(verification="pending")
    )
    with pytest.raises(communityposts_service.NotVerifiedError):
        communityposts_service.create_post("cid1", CommunityPostIn(kind="announcement", title="x"))


def test_create_post_rejects_missing_community(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: None)
    with pytest.raises(communityposts_service.NotFoundError):
        communityposts_service.create_post("cid1", CommunityPostIn(kind="announcement", title="x"))


def test_create_post_resolves_photo_storage_path(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: _verified_community())
    posts = StubPostsCollection()
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubDB(posts))
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )

    payload = CommunityPostIn(
        kind="announcement", title="x", photoStoragePath="communities/cid1/photos/a.jpg"
    )
    communityposts_service.create_post("cid1", payload)

    assert posts.added["imageUrl"] == "https://cdn.example/communities/cid1/photos/a.jpg"


def test_create_post_rejects_foreign_photo_storage_path(monkeypatch):
    monkeypatch.setattr(communities_service, "get_community", lambda cid: _verified_community())
    payload = CommunityPostIn(
        kind="announcement", title="x", photoStoragePath="communities/other-cid/photos/a.jpg"
    )
    with pytest.raises(ValueError):
        communityposts_service.create_post("cid1", payload)


def test_create_post_uses_communitys_first_photo_as_logo(monkeypatch):
    community = _verified_community(
        photos=[{"storagePath": "x", "url": "https://cdn.example/logo.jpg"}]
    )
    monkeypatch.setattr(communities_service, "get_community", lambda cid: community)
    posts = StubPostsCollection()
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubDB(posts))

    communityposts_service.create_post("cid1", CommunityPostIn(kind="announcement", title="x"))

    assert posts.added["communityLogoUrl"] == "https://cdn.example/logo.jpg"


# --- update_post() — ownership check ----------------------------------------


class StubPostSnap:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


class StubPostRef:
    def __init__(self, snap):
        self._snap = snap
        self.last_update = None

    def get(self):
        return self._snap

    def update(self, updates):
        self.last_update = updates


class StubPostsDB:
    def __init__(self, ref):
        self._ref = ref

    def collection(self, name):
        return self

    def document(self, post_id):
        return self._ref


def test_update_post_rejects_post_from_another_community(monkeypatch):
    ref = StubPostRef(StubPostSnap({"communityId": "other-cid"}))
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubPostsDB(ref))
    from app.models.community_post import CommunityPostUpdate

    with pytest.raises(communityposts_service.PostNotFoundError):
        communityposts_service.update_post("cid1", "post-1", CommunityPostUpdate(active=False))


def test_update_post_allows_unpublishing_own_post(monkeypatch):
    ref = StubPostRef(StubPostSnap({"communityId": "cid1"}))
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubPostsDB(ref))
    from app.models.community_post import CommunityPostUpdate

    communityposts_service.update_post("cid1", "post-1", CommunityPostUpdate(active=False))
    assert ref.last_update["active"] is False


# --- list_posts() — owner sees unpublished posts, others don't -------------


class StubListQuery:
    def __init__(self, docs):
        self._docs = docs
        self.filters = []

    def where(self, field, op, value):
        self.filters.append((field, op, value))
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class StubListDoc:
    def __init__(self, post_id, data):
        self.id = post_id
        self._data = data

    def to_dict(self):
        return self._data


class StubListDB:
    def __init__(self, docs):
        self._docs = docs
        self.query = None

    def collection(self, name):
        self.query = StubListQuery(self._docs)
        return self.query

    def get_all(self, refs):
        return []


def test_list_posts_owner_sees_unpublished(monkeypatch):
    docs = [
        StubListDoc("p1", {"communityId": "cid1", "kind": "announcement", "title": "Live", "active": True}),
        StubListDoc("p2", {"communityId": "cid1", "kind": "announcement", "title": "Draft", "active": False}),
    ]
    db = StubListDB(docs)
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: db)

    result = communityposts_service.list_posts("cid1", "cid1")

    assert [p["postId"] for p in result] == ["p1", "p2"]
    assert ("active", "==", True) not in db.query.filters


def test_list_posts_non_owner_only_sees_active(monkeypatch):
    docs = [
        StubListDoc("p1", {"communityId": "cid1", "kind": "announcement", "title": "Live", "active": True}),
    ]
    db = StubListDB(docs)
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: db)

    communityposts_service.list_posts("cid1", "some-member-uid")

    assert ("active", "==", True) in db.query.filters


# --- list_active_for_feed() — verified-communities filter + cache ----------


class StubFeedDoc:
    def __init__(self, post_id, data):
        self.id = post_id
        self._data = data

    def to_dict(self):
        return self._data


class StubFeedQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class StubFeedDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return StubFeedQuery(self._docs)


@pytest.fixture(autouse=True)
def clear_feed_cache():
    communityposts_service._feed_cache.update(expires=0.0, limit=None, posts=None)
    yield
    communityposts_service._feed_cache.update(expires=0.0, limit=None, posts=None)


def test_list_active_for_feed_drops_unverified_communities(monkeypatch):
    docs = [
        StubFeedDoc("p1", {"communityId": "verified-cid", "kind": "announcement", "title": "A", "active": True}),
        StubFeedDoc("p2", {"communityId": "pending-cid", "kind": "announcement", "title": "B", "active": True}),
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))
    monkeypatch.setattr(communities_service, "get_verified_uids", lambda cids: {"verified-cid"})

    result = communityposts_service.list_active_for_feed()

    assert [p["postId"] for p in result] == ["p1"]


def test_list_active_for_feed_drops_past_events(monkeypatch):
    docs = [
        StubFeedDoc(
            "upcoming",
            {
                "communityId": "cid1", "kind": "event", "title": "Rally", "active": True,
                "eventAt": _future_iso(),
            },
        ),
        StubFeedDoc(
            "past",
            {
                "communityId": "cid1", "kind": "event", "title": "Old rally", "active": True,
                "eventAt": _past_iso(),
            },
        ),
        StubFeedDoc(
            "announcement",
            {"communityId": "cid1", "kind": "announcement", "title": "Notice", "active": True},
        ),
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))
    monkeypatch.setattr(communities_service, "get_verified_uids", lambda cids: {"cid1"})

    result = communityposts_service.list_active_for_feed()

    assert {p["postId"] for p in result} == {"upcoming", "announcement"}


def test_list_active_for_feed_is_cached(monkeypatch):
    calls = {"count": 0}

    def counting_db():
        calls["count"] += 1
        return StubFeedDB(
            [StubFeedDoc("p1", {"communityId": "c1", "kind": "announcement", "title": "A", "active": True})]
        )

    monkeypatch.setattr(communityposts_service, "get_firestore", counting_db)
    monkeypatch.setattr(communities_service, "get_verified_uids", lambda cids: {"c1"})

    first = communityposts_service.list_active_for_feed()
    second = communityposts_service.list_active_for_feed()

    assert first == second
    assert calls["count"] == 1
