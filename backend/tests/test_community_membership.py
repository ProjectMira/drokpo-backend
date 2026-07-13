from conftest import TEST_UID


def test_join_community(client, monkeypatch):
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.join_community", lambda cid, uid: captured.update(cid=cid, uid=uid)
    )
    response = client.post("/api/communities/some-cid/join")
    assert response.status_code == 200
    assert captured == {"cid": "some-cid", "uid": TEST_UID}


def test_join_community_rejects_community_accounts(client, monkeypatch):
    # require_person_uid (app/dependencies.py) gates join/leave now; a
    # community account has no users/{uid} doc, so this is what makes it 403.
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    response = client.post("/api/communities/some-cid/join")
    assert response.status_code == 403


def test_join_nonexistent_community(client, monkeypatch):
    from app.services.communities import NotFoundError

    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)

    def fail(cid, uid):
        raise NotFoundError("Community not found")

    monkeypatch.setattr("app.services.communities.join_community", fail)
    response = client.post("/api/communities/nope/join")
    assert response.status_code == 404


def test_leave_community(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.leave_community", lambda cid, uid: captured.update(cid=cid, uid=uid)
    )
    response = client.delete("/api/communities/some-cid/join")
    assert response.status_code == 200
    assert captured == {"cid": "some-cid", "uid": TEST_UID}


def test_list_communities_directory(client, monkeypatch):
    communities = [
        {"uid": "c1", "name": "A", "memberCount": 100, "joined": True},
        {"uid": "c2", "name": "B", "memberCount": 10, "joined": False},
    ]
    monkeypatch.setattr("app.services.communities.list_directory", lambda uid, limit: communities)
    response = client.get("/api/communities")
    assert response.status_code == 200
    assert response.json() == {"communities": communities}


def test_get_community_detail(client, monkeypatch):
    card = {"uid": "c1", "name": "A", "verification": "verified", "joined": False}
    monkeypatch.setattr("app.services.communities.get_community_card", lambda uid, cid: card)
    response = client.get("/api/communities/c1")
    assert response.status_code == 200
    assert response.json() == card


def test_get_community_detail_not_found(client, monkeypatch):
    monkeypatch.setattr("app.services.communities.get_community_card", lambda uid, cid: None)
    response = client.get("/api/communities/nope")
    assert response.status_code == 404


def test_list_my_communities(client, monkeypatch):
    mine = [{"uid": "c1", "name": "A", "joined": True}]
    monkeypatch.setattr("app.services.communities.list_my_communities", lambda uid: mine)
    response = client.get("/api/communities/mine")
    assert response.status_code == 200
    assert response.json() == {"communities": mine}


def test_mine_route_not_shadowed_by_cid_route(client, monkeypatch):
    # Regression guard: "/communities/mine" must hit the literal /mine route,
    # not "/communities/{cid}" with cid="mine".
    called = {"card": False}

    def fake_card(uid, cid):
        called["card"] = True
        return None

    monkeypatch.setattr("app.services.communities.get_community_card", fake_card)
    monkeypatch.setattr("app.services.communities.list_my_communities", lambda uid: [])
    response = client.get("/api/communities/mine")
    assert response.status_code == 200
    assert called["card"] is False
