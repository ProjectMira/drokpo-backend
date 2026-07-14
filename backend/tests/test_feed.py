import pytest


@pytest.fixture(autouse=True)
def no_content_cards(monkeypatch):
    # Feed responses include active ads/news/community posts; default them to
    # none so candidate tests don't touch Firestore. Overridden in the
    # content-specific tests.
    monkeypatch.setattr("app.services.ads.list_active", lambda: [])
    monkeypatch.setattr("app.services.news.list_active", lambda: [])
    monkeypatch.setattr("app.services.communityposts.list_active_for_feed", lambda: [])


def test_feed_requires_person_account(client, monkeypatch):
    # No users/{uid} doc at all: require_person_uid rejects before the
    # route's own "complete onboarding" check ever runs (a uid with zero
    # profile isn't mid-onboarding, it's not a person account).
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
