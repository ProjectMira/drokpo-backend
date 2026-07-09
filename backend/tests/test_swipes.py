from app.services.matching import BlockedError
from conftest import TEST_UID


def test_swipe_creates_match(client, monkeypatch):
    monkeypatch.setattr("app.services.matching.record_swipe", lambda f, t, a: "uidA_uidB")
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 200
    assert response.json() == {"matched": True, "matchId": "uidA_uidB"}


def test_swipe_no_match(client, monkeypatch):
    monkeypatch.setattr("app.services.matching.record_swipe", lambda f, t, a: None)
    response = client.post("/api/swipes/other-uid", json={"action": "pass"})
    assert response.json() == {"matched": False, "matchId": None}


def test_swipe_on_self_rejected(client):
    response = client.post(f"/api/swipes/{TEST_UID}", json={"action": "like"})
    assert response.status_code == 400


def test_swipe_blocked_pair_rejected(client, monkeypatch):
    def blocked(f, t, a):
        raise BlockedError("Cannot swipe on this user")

    monkeypatch.setattr("app.services.matching.record_swipe", blocked)
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 403


def test_swipe_invalid_action(client):
    response = client.post("/api/swipes/other-uid", json={"action": "wink"})
    assert response.status_code == 422


def test_list_swipes(client, monkeypatch):
    swipes = [{"uid": "u2", "action": "like"}, {"uid": "u3", "action": "pass"}]
    monkeypatch.setattr("app.services.matching.list_swipes", lambda uid, action, limit: swipes)
    response = client.get("/api/swipes")
    assert response.status_code == 200
    assert response.json() == {"swipes": swipes}


def test_list_likes_sent(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.matching.list_swipes",
        lambda uid, action, limit: captured.update(uid=uid, action=action, limit=limit) or [],
    )
    response = client.get("/api/swipes", params={"action": "like", "limit": 10})
    assert response.status_code == 200
    assert captured == {"uid": TEST_UID, "action": "like", "limit": 10}


def test_list_swipes_invalid_action(client):
    assert client.get("/api/swipes", params={"action": "wink"}).status_code == 422
