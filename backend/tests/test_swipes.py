from app.services.matching import BlockedError
from conftest import TEST_UID


def test_swipe_creates_match(client, monkeypatch):
    monkeypatch.setattr("app.services.matching.record_swipe", lambda f, t, a: "uidA_uidB")
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 200
    assert response.json() == {"matched": True, "matchId": "uidA_uidB"}


# --- community accounts swipe as themselves, once verified -------------------


def _as_verified_community(monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr(
        "app.services.communities.get_community",
        lambda uid: {"uid": uid, "name": "TANY", "verification": "verified"},
    )


def test_verified_community_can_swipe(client, monkeypatch):
    _as_verified_community(monkeypatch)
    monkeypatch.setattr("app.services.matching.record_swipe", lambda f, t, a: None)
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 200
    assert response.json() == {"matched": False, "matchId": None}


def test_unverified_community_cannot_swipe(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr(
        "app.services.communities.get_community",
        lambda uid: {"uid": uid, "name": "TANY", "verification": "pending"},
    )
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 403
    assert "verified" in response.json()["detail"]


def test_community_list_swipes_and_received_allowed(client, monkeypatch):
    _as_verified_community(monkeypatch)
    monkeypatch.setattr("app.services.matching.list_swipes", lambda uid, action, limit: [])
    monkeypatch.setattr("app.services.matching.list_received", lambda uid, action, limit: [])
    assert client.get("/api/swipes").status_code == 200
    assert client.get("/api/swipes/received").status_code == 200


def test_swipe_rejects_neither_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    response = client.post("/api/swipes/other-uid", json={"action": "like"})
    assert response.status_code == 403


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


def test_list_received_swipes(client, monkeypatch):
    captured = {}
    received = [{"uid": "u9", "action": "like", "otherUser": {"uid": "u9", "displayName": "Dolma"}}]
    monkeypatch.setattr(
        "app.services.matching.list_received",
        lambda uid, action, limit: captured.update(uid=uid, action=action, limit=limit) or received,
    )
    response = client.get("/api/swipes/received?action=like")
    assert response.status_code == 200
    assert response.json() == {"swipes": received}
    assert captured == {"uid": TEST_UID, "action": "like", "limit": 100}


def test_list_received_invalid_action(client):
    assert client.get("/api/swipes/received?action=wink").status_code == 422


def test_attach_match_state(monkeypatch):
    """Each swipe gets matchId set only when the counterpart match is active,
    and matchId null (with the raw status) otherwise."""
    from app.services import matching as matching_service
    from app.services.matching import _match_id

    match_docs = {
        _match_id("me", "matched-active"): {"status": "active"},
        _match_id("me", "matched-ended"): {"status": "unmatched"},
        # "no-match" has no doc at all.
    }

    class StubSnap:
        def __init__(self, doc_id, data):
            self.id = doc_id
            self._data = data
            self.exists = data is not None

        def to_dict(self):
            return self._data

    class StubRef:
        def __init__(self, doc_id):
            self.id = doc_id

    class StubCollection:
        def document(self, doc_id):
            return StubRef(doc_id)

    class StubDB:
        def collection(self, name):
            assert name == "matches"
            return StubCollection()

        def get_all(self, refs):
            return [StubSnap(ref.id, match_docs.get(ref.id)) for ref in refs]

    swipes = [
        {"uid": "matched-active"},
        {"uid": "matched-ended"},
        {"uid": "no-match"},
    ]
    result = matching_service._attach_match_state(StubDB(), "me", swipes)

    by_uid = {s["uid"]: s for s in result}
    assert by_uid["matched-active"]["matchId"] == _match_id("me", "matched-active")
    assert by_uid["matched-active"]["matchStatus"] == "active"
    assert by_uid["matched-ended"]["matchId"] is None
    assert by_uid["matched-ended"]["matchStatus"] == "unmatched"
    assert by_uid["no-match"]["matchId"] is None
    assert by_uid["no-match"]["matchStatus"] is None


def test_attach_match_state_empty_list():
    from app.services import matching as matching_service

    assert matching_service._attach_match_state(object(), "me", []) == []


def test_undo_swipe(client, monkeypatch):
    undone = {}
    monkeypatch.setattr(
        "app.services.matching.undo_swipe", lambda f, t: undone.update(from_uid=f, to_uid=t)
    )
    response = client.delete("/api/swipes/u2")
    assert response.status_code == 200
    assert undone == {"from_uid": "test-uid", "to_uid": "u2"}


def test_undo_swipe_refused_after_match(client, monkeypatch):
    from app.services.matching import MatchedError

    def raise_matched(f, t):
        raise MatchedError("You already matched")

    monkeypatch.setattr("app.services.matching.undo_swipe", raise_matched)
    assert client.delete("/api/swipes/u2").status_code == 409
