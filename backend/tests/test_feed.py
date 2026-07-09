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
    assert response.json() == {"candidates": candidates}


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
