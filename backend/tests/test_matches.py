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
