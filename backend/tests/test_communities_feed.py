"""Joined-communities feed (list_feed_for_member) and members-list coverage."""

from app.services import communities as communities_service
from app.services import communityposts as communityposts_service
from conftest import TEST_UID


# --- list_feed_for_member() — chunking, merge, re-sort ----------------------


class StubFeedDoc:
    def __init__(self, post_id, data):
        self.id = post_id
        self._data = data

    def to_dict(self):
        return self._data


class StubFeedQuery:
    def __init__(self, all_docs):
        self._all_docs = all_docs
        self._community_ids = None
        self._active_only = False
        self._limit = None

    def where(self, field, op, value):
        if field == "communityId" and op == "in":
            self._community_ids = set(value)
        if field == "active":
            self._active_only = True
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        docs = [d for d in self._all_docs if d._data.get("communityId") in (self._community_ids or set())]
        if self._active_only:
            docs = [d for d in docs if d._data.get("active")]
        # Real Firestore orders server-side before applying limit; do the
        # same here so a per-chunk cap is faithfully simulated.
        docs.sort(key=lambda d: d._data.get("createdAt"), reverse=True)
        if self._limit is not None:
            docs = docs[: self._limit]
        return iter(docs)


class StubFeedDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return StubFeedQuery(self._docs)


def test_list_feed_for_member_empty_when_no_memberships(monkeypatch):
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: [])
    assert communityposts_service.list_feed_for_member("uid1") == []


def test_list_feed_for_member_sorts_newest_first(monkeypatch):
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: ["c1", "c2"])
    docs = [
        StubFeedDoc("p1", {"communityId": "c1", "kind": "announcement", "title": "A", "active": True, "createdAt": 2}),
        StubFeedDoc("p2", {"communityId": "c2", "kind": "announcement", "title": "B", "active": True, "createdAt": 1}),
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))

    result = communityposts_service.list_feed_for_member("uid1")

    assert [p["postId"] for p in result] == ["p1", "p2"]


def test_list_feed_for_member_ignores_inactive_posts(monkeypatch):
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: ["c1"])
    docs = [
        StubFeedDoc("live", {"communityId": "c1", "kind": "announcement", "title": "A", "active": True, "createdAt": 1}),
        StubFeedDoc("draft", {"communityId": "c1", "kind": "announcement", "title": "B", "active": False, "createdAt": 2}),
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))

    result = communityposts_service.list_feed_for_member("uid1")

    assert [p["postId"] for p in result] == ["live"]


def test_list_feed_for_member_chunks_over_thirty_communities(monkeypatch):
    community_ids = [f"c{i}" for i in range(35)]
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: community_ids)
    docs = [
        StubFeedDoc(
            f"p{i}",
            {"communityId": f"c{i}", "kind": "announcement", "title": f"Post {i}", "active": True, "createdAt": i},
        )
        for i in range(35)
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))

    result = communityposts_service.list_feed_for_member("uid1", limit=50)

    assert len(result) == 35
    assert result[0]["postId"] == "p34"


def test_list_feed_for_member_merges_and_resorts_across_chunks(monkeypatch):
    # 31 communities -> 2 chunks (30 + 1). The single newest post lives in a
    # community only reachable in the 2nd chunk, so this proves results are
    # merged and re-sorted across chunks, not just concatenated in order.
    community_ids = [f"c{i}" for i in range(31)]
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: community_ids)
    docs = [
        StubFeedDoc(
            f"p{i}",
            {"communityId": f"c{i}", "kind": "announcement", "title": f"Post {i}", "active": True, "createdAt": i},
        )
        for i in range(30)
    ]
    docs.append(
        StubFeedDoc(
            "p_newest",
            {"communityId": "c30", "kind": "announcement", "title": "Newest", "active": True, "createdAt": 1000},
        )
    )
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))

    result = communityposts_service.list_feed_for_member("uid1", limit=3)

    assert result[0]["postId"] == "p_newest"
    assert len(result) == 3


def test_list_feed_for_member_hydrates_my_rsvp(monkeypatch):
    monkeypatch.setattr(communities_service, "get_joined_community_ids", lambda uid: ["c1"])
    docs = [
        StubFeedDoc(
            "event1",
            {"communityId": "c1", "kind": "event", "title": "Rally", "active": True, "createdAt": 1, "attendeeCount": 1},
        ),
    ]
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: StubFeedDB(docs))
    monkeypatch.setattr(communityposts_service, "_hydrate_my_rsvps", lambda db, uid, posts: {"event1"})
    monkeypatch.setattr(communityposts_service, "_hydrate_my_votes", lambda db, uid, posts: {})

    result = communityposts_service.list_feed_for_member("uid1")

    assert result[0]["myRsvp"] is True


# --- is_member_or_self() / list_members() -----------------------------------


class StubExistsSnap:
    def __init__(self, exists):
        self.exists = exists


class StubMembershipDB:
    """Just enough chained-method surface for is_member_or_self's single
    exists check: db.collection(...).document(cid).collection(...).document(uid).get()"""

    def __init__(self, exists):
        self._exists = exists

    def collection(self, name):
        return self

    def document(self, uid):
        return self

    def get(self):
        return StubExistsSnap(self._exists)


def test_is_member_or_self_true_for_self():
    # No Firestore call needed at all when uid == cid.
    assert communities_service.is_member_or_self("cid1", "cid1") is True


def test_is_member_or_self_true_for_member(monkeypatch):
    monkeypatch.setattr(communities_service, "get_firestore", lambda: StubMembershipDB(True))
    assert communities_service.is_member_or_self("member-uid", "cid1") is True


def test_is_member_or_self_false_for_non_member(monkeypatch):
    monkeypatch.setattr(communities_service, "get_firestore", lambda: StubMembershipDB(False))
    assert communities_service.is_member_or_self("stranger-uid", "cid1") is False


class StubMembersDB:
    """Chained-method surface for list_members' query:
    db.collection(...).document(cid).collection(...).order_by(...).limit(...).stream()
    — every method but stream() is an identity op, so one class covers the chain."""

    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return self

    def document(self, cid):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class StubMemberDoc:
    def __init__(self, uid):
        self.id = uid


def test_list_members_returns_slim_profiles_in_query_order(monkeypatch):
    docs = [StubMemberDoc("u1"), StubMemberDoc("u2")]
    monkeypatch.setattr(communities_service, "get_firestore", lambda: StubMembersDB(docs))
    monkeypatch.setattr(
        "app.services.users.get_public_profiles",
        lambda uids: {
            "u1": {"displayName": "Tenzin", "photos": [{"storagePath": "x", "url": "https://a"}], "region": "India",
                   "bio": "secret bio", "socials": {"instagram": "hidden"}},
            "u2": {"displayName": "Dolma", "photos": [], "region": None},
        },
    )

    result = communities_service.list_members("cid1")

    assert result == [
        {"uid": "u1", "displayName": "Tenzin", "photo": {"storagePath": "x", "url": "https://a"}, "region": "India"},
        {"uid": "u2", "displayName": "Dolma", "photo": None, "region": None},
    ]
    # Never the full dating-card view — bio/socials must not leak here.
    assert "bio" not in result[0] and "socials" not in result[0]


def test_list_members_skips_uids_with_no_profile(monkeypatch):
    docs = [StubMemberDoc("u1"), StubMemberDoc("deleted-user")]
    monkeypatch.setattr(communities_service, "get_firestore", lambda: StubMembersDB(docs))
    monkeypatch.setattr(
        "app.services.users.get_public_profiles",
        lambda uids: {"u1": {"displayName": "Tenzin", "photos": [], "region": "India"}},
    )

    result = communities_service.list_members("cid1")

    assert [m["uid"] for m in result] == ["u1"]


# --- router-level -----------------------------------------------------------


def test_get_joined_communities_feed(client, monkeypatch):
    posts = [{"postId": "p1", "title": "Losar"}]
    monkeypatch.setattr("app.services.communityposts.list_feed_for_member", lambda uid, limit: posts)
    response = client.get("/api/communities/feed")
    assert response.status_code == 200
    assert response.json() == {"posts": posts}


def test_joined_communities_feed_requires_person(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    response = client.get("/api/communities/feed")
    assert response.status_code == 403


def test_feed_route_not_shadowed_by_cid_route(client, monkeypatch):
    # Regression guard: "/communities/feed" must hit the literal /feed route,
    # not "/communities/{cid}" with cid="feed".
    called = {"card": False}

    def fake_card(uid, cid):
        called["card"] = True
        return None

    monkeypatch.setattr("app.services.communities.get_community_card", fake_card)
    monkeypatch.setattr("app.services.communityposts.list_feed_for_member", lambda uid, limit: [])
    response = client.get("/api/communities/feed")
    assert response.status_code == 200
    assert called["card"] is False


def test_list_members_endpoint(client, monkeypatch):
    members = [{"uid": "u1", "displayName": "Tenzin", "photo": None, "region": "India"}]
    monkeypatch.setattr("app.services.communities.is_member_or_self", lambda uid, cid: True)
    monkeypatch.setattr("app.services.communities.list_members", lambda cid, limit: members)
    response = client.get("/api/communities/cid1/members")
    assert response.status_code == 200
    assert response.json() == {"members": members}


def test_list_members_rejects_non_members(client, monkeypatch):
    monkeypatch.setattr("app.services.communities.is_member_or_self", lambda uid, cid: False)
    response = client.get("/api/communities/cid1/members")
    assert response.status_code == 403


def test_list_members_allows_the_community_itself(client, monkeypatch):
    # is_member_or_self handles uid == cid, so the community viewing its own
    # members works even though the endpoint isn't role-gated either way.
    monkeypatch.setattr("app.services.communities.is_member_or_self", lambda uid, cid: uid == cid)
    monkeypatch.setattr("app.services.communities.list_members", lambda cid, limit: [])
    response = client.get(f"/api/communities/{TEST_UID}/members")
    assert response.status_code == 200
