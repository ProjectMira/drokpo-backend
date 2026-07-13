"""Cross-cutting coverage for the require_person_uid/require_community_uid
dependencies (app/dependencies.py) — one representative endpoint per role,
checked in both directions, rather than re-testing this in every router's
own test file."""

from conftest import TEST_UID


def _as_person(monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"uid": uid})
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)


def _as_community(monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)


def _as_neither(monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)


# --- require_person_uid: person-only endpoints ------------------------------


def test_person_endpoint_allows_person(client, monkeypatch):
    _as_person(monkeypatch)
    monkeypatch.setattr("app.services.matches.list_for_user", lambda uid: [])
    assert client.get("/api/matches").status_code == 200


def test_person_endpoint_rejects_community(client, monkeypatch):
    _as_community(monkeypatch)
    response = client.get("/api/matches")
    assert response.status_code == 403
    assert "personal account" in response.json()["detail"]


def test_person_endpoint_rejects_neither(client, monkeypatch):
    _as_neither(monkeypatch)
    assert client.get("/api/matches").status_code == 403


# --- require_community_uid: community-only endpoints ------------------------


def test_community_endpoint_allows_community(client, monkeypatch):
    _as_community(monkeypatch)
    monkeypatch.setattr(
        "app.services.communities.get_community", lambda uid: {"uid": uid, "name": "TANY"}
    )
    assert client.get("/api/communities/me").status_code == 200


def test_community_endpoint_rejects_person(client, monkeypatch):
    _as_person(monkeypatch)
    response = client.get("/api/communities/me")
    assert response.status_code == 403
    assert "community account" in response.json()["detail"]


def test_community_endpoint_rejects_neither(client, monkeypatch):
    _as_neither(monkeypatch)
    assert client.get("/api/communities/me").status_code == 403


# --- auth failures happen before either role check runs ---------------------


def test_missing_auth_header_rejected_before_role_check(anon_client):
    # require_person_uid/require_community_uid both depend on get_current_uid;
    # a missing Authorization header fails there (422, same as any other
    # protected endpoint) before either role check ever runs.
    assert anon_client.get("/api/matches").status_code == 422


def test_non_bearer_token_rejected_before_role_check(anon_client):
    response = anon_client.get("/api/matches", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
