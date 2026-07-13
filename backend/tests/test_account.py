def test_get_account_person(client, monkeypatch):
    profile = {"uid": "test-uid", "displayName": "Tenzin", "onboardingComplete": True}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: profile)
    response = client.get("/api/account")
    assert response.status_code == 200
    assert response.json() == {"accountType": "person", "profile": profile, "community": None}


def test_get_account_community(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    community = {"uid": "test-uid", "name": "TANY", "verification": "pending"}
    monkeypatch.setattr("app.services.communities.get_community", lambda uid: community)
    response = client.get("/api/account")
    assert response.status_code == 200
    assert response.json() == {"accountType": "community", "profile": None, "community": community}


def test_get_account_none(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.get_community", lambda uid: None)
    response = client.get("/api/account")
    assert response.status_code == 200
    assert response.json() == {"accountType": "none", "profile": None, "community": None}


def test_get_account_person_takes_precedence(client, monkeypatch):
    # A uid should never have both docs, but if it somehow did, person wins
    # rather than the endpoint erroring.
    profile = {"uid": "test-uid"}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: profile)
    monkeypatch.setattr("app.services.communities.get_community", lambda uid: {"uid": "test-uid"})
    response = client.get("/api/account")
    assert response.json()["accountType"] == "person"
