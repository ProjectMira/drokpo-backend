import pytest


@pytest.fixture(autouse=True)
def no_content_cards(monkeypatch):
    # Feed responses include active ads/news/community posts; default them to
    # none so candidate tests don't touch Firestore. Overridden in the
    # content-specific tests.
    monkeypatch.setattr("app.services.ads.list_active", lambda: [])
    monkeypatch.setattr("app.services.news.list_active", lambda: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: [])
    # The route drops content the viewer already liked; default to none liked.
    monkeypatch.setattr("app.services.news.liked_ids", lambda uid: set())
    monkeypatch.setattr("app.services.communityposts.liked_ids", lambda uid: set())
    # ...and posts from blocked communities; default to nobody blocked.
    monkeypatch.setattr("app.routers.feed.get_firestore", lambda: None)
    monkeypatch.setattr("app.services.users._blocked_uids", lambda db, uid: set())


def test_feed_requires_an_account(client, monkeypatch):
    # No users/{uid} and no communities/{uid} doc: require_account_uid
    # rejects before the route body ever runs (see the community-viewer
    # tests below for the "has a communities/{uid} doc" branch).
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    assert client.get("/api/feed").status_code == 403


def test_feed_requires_completed_onboarding(client, monkeypatch):
    # Profile exists (require_person_uid passes) but onboarding isn't done —
    # this is the route body's own check, distinct from the case above.
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": False})
    assert client.get("/api/feed").status_code == 400


def test_feed_returns_candidates(client, monkeypatch):
    profile = {"onboardingComplete": True, "interests": ["hiking"]}
    candidates = [{"uid": "u2", "displayName": "Dolma", "interests": ["hiking", "gorshey"]}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: profile)
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: candidates)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": candidates, "ads": [], "news": [], "communityPosts": []}


def test_feed_includes_active_ads(client, monkeypatch):
    ads = [{"adId": "ad1", "title": "Momo House", "linkUrl": "https://momohouse.example"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.ads.list_active", lambda: ads)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "ads": ads, "news": [], "communityPosts": []}


def test_feed_includes_active_news(client, monkeypatch):
    news = [{"newsId": "n1", "title": "Headline", "gist": "Gist", "sourceUrl": "https://src.example"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.news.list_active", lambda: news)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "ads": [], "news": news, "communityPosts": []}


def test_feed_includes_active_community_posts(client, monkeypatch):
    posts = [{"postId": "p1", "kind": "announcement", "title": "Losar"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "ads": [], "news": [], "communityPosts": posts}


def test_feed_excludes_already_liked_news(client, monkeypatch):
    # A saved story lives in the Likes tab; the deck must not cycle it back.
    news = [
        {"newsId": "n1", "title": "Saved", "gist": "G", "sourceUrl": "https://s.example"},
        {"newsId": "n2", "title": "Fresh", "gist": "G", "sourceUrl": "https://s.example"},
    ]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.news.list_active", lambda: news)
    monkeypatch.setattr("app.services.news.liked_ids", lambda uid: {"n1"})
    body = client.get("/api/feed").json()
    assert [n["newsId"] for n in body["news"]] == ["n2"]
    # The shared cache list itself must be untouched.
    assert [n["newsId"] for n in news] == ["n1", "n2"]


def test_feed_excludes_already_liked_posts(client, monkeypatch):
    posts = [
        {"postId": "p1", "kind": "announcement", "title": "Saved"},
        {"postId": "p2", "kind": "announcement", "title": "Fresh"},
    ]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)
    monkeypatch.setattr("app.services.communityposts.liked_ids", lambda uid: {"p1"})
    body = client.get("/api/feed").json()
    assert [p["postId"] for p in body["communityPosts"]] == ["p2"]
    assert [p["postId"] for p in posts] == ["p1", "p2"]


def test_feed_excludes_posts_from_blocked_communities(client, monkeypatch):
    posts = [
        {"postId": "p1", "kind": "announcement", "communityId": "blocked-cid"},
        {"postId": "p2", "kind": "announcement", "communityId": "friendly-cid"},
    ]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)
    monkeypatch.setattr("app.services.users._blocked_uids", lambda db, uid: {"blocked-cid"})
    body = client.get("/api/feed").json()
    assert [p["postId"] for p in body["communityPosts"]] == ["p2"]
    assert [p["postId"] for p in posts] == ["p1", "p2"]


def test_feed_community_posts_carry_viewer_vote_and_rsvp(client, monkeypatch):
    # The cached posts are viewer-agnostic; the route must overlay the
    # caller's own poll vote / event RSVP (attach_viewer_state).
    posts = [
        {"postId": "p1", "kind": "poll", "title": "Pick a date"},
        {"postId": "p2", "kind": "event", "title": "Losar", "attendeeCount": 3},
        {"postId": "p3", "kind": "announcement", "title": "News"},
    ]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)
    monkeypatch.setattr("app.services.communityposts.get_firestore", lambda: None)
    monkeypatch.setattr(
        "app.services.communityposts._hydrate_my_votes", lambda db, uid, p: {"p1": "opt2"}
    )
    monkeypatch.setattr("app.services.communityposts._hydrate_my_rsvps", lambda db, uid, p: {"p2"})
    body = client.get("/api/feed").json()
    by_id = {p["postId"]: p for p in body["communityPosts"]}
    assert by_id["p1"]["myVote"] == "opt2"
    assert by_id["p2"]["myRsvp"] is True
    assert "myVote" not in by_id["p3"] and "myRsvp" not in by_id["p3"]
    # The shared cached dicts must NOT have been mutated with per-user state.
    assert "myVote" not in posts[0] and "myRsvp" not in posts[1]


def test_feed_announcement_only_posts_skip_hydration(client, monkeypatch):
    # No poll/event in the page → attach_viewer_state must not touch Firestore.
    posts = [{"postId": "p1", "kind": "announcement", "title": "Losar"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)

    def explode():
        raise AssertionError("get_firestore must not be called")

    monkeypatch.setattr("app.services.communityposts.get_firestore", explode)
    assert client.get("/api/feed").json()["communityPosts"] == posts


def test_feed_items_shape(client, monkeypatch):
    candidates = [{"uid": f"u{i}"} for i in range(3)]
    news = [{"newsId": "n1", "title": "Headline", "gist": "Gist", "sourceUrl": "https://s.example"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: candidates)
    monkeypatch.setattr("app.services.news.list_active", lambda: news)
    body = client.get("/api/feed", params={"shape": "items"}).json()
    assert set(body.keys()) == {"items"}
    types = [item["type"] for item in body["items"]]
    assert types[:4] == ["person", "person", "person", "news"]
    assert body["items"][3]["data"]["newsId"] == "n1"


def test_feed_items_shape_never_empty_without_candidates(client, monkeypatch):
    news = [
        {"newsId": f"n{i}", "title": "T", "gist": "G", "sourceUrl": "https://s.example"}
        for i in range(20)
    ]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.news.list_active", lambda: news)
    body = client.get("/api/feed", params={"shape": "items"}).json()
    assert len(body["items"]) == 12
    assert all(item["type"] == "news" for item in body["items"])


def test_feed_default_shape_unchanged(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    body = client.get("/api/feed").json()
    assert set(body.keys()) == {"candidates", "ads", "news", "communityPosts"}


def test_feed_limit_capped(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    assert client.get("/api/feed", params={"limit": 51}).status_code == 422


def test_feed_passes_limit(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr(
        "app.services.users.get_candidates",
        lambda uid, prof, limit: seen.update(limit=limit) or [],
    )
    assert client.get("/api/feed", params={"limit": 5}).status_code == 200
    assert seen["limit"] == 5


# --- community viewers ---------------------------------------------------
#
# A community account has no users/{uid} profile, so it hits the "else"
# branch: no onboarding gate, person candidates only once verified, and its
# own posts filtered out of the mix.


def _as_community(monkeypatch, verification="verified"):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr(
        "app.services.communities.get_community",
        lambda uid: {"uid": uid, "verification": verification},
    )


def test_feed_community_viewer_not_onboarding_gated(client, monkeypatch):
    # No onboardingComplete check applies to a community — it has no such field.
    _as_community(monkeypatch)
    monkeypatch.setattr("app.services.users.get_candidates_for_community", lambda uid, limit: [])
    assert client.get("/api/feed").status_code == 200


def test_feed_verified_community_gets_candidates(client, monkeypatch):
    _as_community(monkeypatch, verification="verified")
    candidates = [{"uid": "u2", "displayName": "Dolma"}]
    monkeypatch.setattr(
        "app.services.users.get_candidates_for_community", lambda uid, limit: candidates
    )
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json()["candidates"] == candidates


def test_feed_unverified_community_gets_no_candidates(client, monkeypatch):
    _as_community(monkeypatch, verification="pending")

    def explode(uid, limit):
        raise AssertionError("get_candidates_for_community must not be called when unverified")

    monkeypatch.setattr("app.services.users.get_candidates_for_community", explode)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json()["candidates"] == []


def test_feed_community_viewer_excludes_own_posts(client, monkeypatch):
    _as_community(monkeypatch)
    monkeypatch.setattr("app.services.users.get_candidates_for_community", lambda uid, limit: [])
    posts = [
        {"postId": "p1", "kind": "announcement", "communityId": "test-uid"},
        {"postId": "p2", "kind": "announcement", "communityId": "other-community"},
    ]
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: posts)
    response = client.get("/api/feed")
    assert [p["postId"] for p in response.json()["communityPosts"]] == ["p2"]
    # The shared cache list itself must be untouched (own-post filter runs on
    # a copy, not the cached list).
    assert [p["postId"] for p in posts] == ["p1", "p2"]


def test_feed_community_viewer_items_shape(client, monkeypatch):
    _as_community(monkeypatch)
    candidates = [{"uid": "u2"}]
    monkeypatch.setattr(
        "app.services.users.get_candidates_for_community", lambda uid, limit: candidates
    )
    body = client.get("/api/feed", params={"shape": "items"}).json()
    assert set(body.keys()) == {"items"}
    assert any(item["type"] == "person" for item in body["items"])
