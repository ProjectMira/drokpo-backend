import pytest


@pytest.fixture(autouse=True)
def no_ads(monkeypatch):
    # Feed responses include active ads; default them to none so candidate
    # tests don't touch Firestore. Overridden in the ad-specific tests.
    monkeypatch.setattr("app.services.ads.list_active", lambda: [])


def test_feed_requires_profile(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    assert client.get("/api/feed").status_code == 400


def test_feed_requires_completed_onboarding(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": False})
    assert client.get("/api/feed").status_code == 400


def test_feed_returns_candidates(client, monkeypatch):
    profile = {"onboardingComplete": True, "interests": ["hiking"]}
    candidates = [{"uid": "u2", "displayName": "Dolma", "interests": ["hiking", "gorshey"]}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: profile)
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: candidates)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": candidates, "ads": []}


def test_feed_includes_active_ads(client, monkeypatch):
    ads = [{"adId": "ad1", "title": "Momo House", "linkUrl": "https://momohouse.example"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"onboardingComplete": True})
    monkeypatch.setattr("app.services.users.get_candidates", lambda uid, prof, limit: [])
    monkeypatch.setattr("app.services.ads.list_active", lambda: ads)
    response = client.get("/api/feed")
    assert response.status_code == 200
    assert response.json() == {"candidates": [], "ads": ads}


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
