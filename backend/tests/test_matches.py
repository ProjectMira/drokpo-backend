def test_list_matches(client, monkeypatch):
    matches = [{"matchId": "a_b", "users": ["a", "b"], "status": "active"}]
    monkeypatch.setattr("app.services.matches.list_for_user", lambda uid: matches)
    response = client.get("/api/matches")
    assert response.status_code == 200
    assert response.json() == {"matches": matches}


def test_unmatch(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.matches.unmatch", lambda match_id, uid: captured.update(m=match_id, u=uid)
    )
    response = client.post("/api/matches/a_b/unmatch")
    assert response.status_code == 200
    assert captured["m"] == "a_b"


def test_unmatch_not_found(client, monkeypatch):
    def missing(match_id, uid):
        raise ValueError("Match not found")

    monkeypatch.setattr("app.services.matches.unmatch", missing)
    assert client.post("/api/matches/nope/unmatch").status_code == 404


def test_list_matches_allows_community_account(client, monkeypatch):
    # Community accounts match/chat as themselves — /api/matches accepts
    # either role (require_account_uid).
    matches = [{"matchId": "person_c1", "users": ["person", "c1"], "status": "active"}]
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr("app.services.matches.list_for_user", lambda uid: matches)
    response = client.get("/api/matches")
    assert response.status_code == 200
    assert response.json() == {"matches": matches}


# --- service-level: list_for_user joins a community counterpart -------------


class _StubSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class _StubQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        return self

    def stream(self):
        return iter(self._docs)


class _StubDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        assert name == "matches"
        return _StubQuery(self._docs)


def test_list_for_user_includes_community_counterpart(monkeypatch):
    from app.services import matches as matches_service

    monkeypatch.setattr(
        matches_service,
        "get_firestore",
        lambda: _StubDB([_StubSnap("me_c1", {"users": ["me", "c1"], "status": "active"})]),
    )
    monkeypatch.setattr(
        "app.services.counterparts.get_public_counterparts",
        lambda uids: {"c1": {"uid": "c1", "kind": "community", "displayName": "TANY"}},
    )
    result = matches_service.list_for_user("me")
    assert len(result) == 1
    assert result[0]["otherUser"] == {"uid": "c1", "kind": "community", "displayName": "TANY"}


def test_list_for_user_drops_counterpart_missing_from_both_collections(monkeypatch):
    from app.services import matches as matches_service

    monkeypatch.setattr(
        matches_service,
        "get_firestore",
        lambda: _StubDB([_StubSnap("me_gone", {"users": ["me", "gone"], "status": "active"})]),
    )
    monkeypatch.setattr("app.services.counterparts.get_public_counterparts", lambda uids: {})
    assert matches_service.list_for_user("me") == []
