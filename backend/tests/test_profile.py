from conftest import TEST_UID


def test_get_my_profile(client, monkeypatch):
    profile = {"uid": TEST_UID, "displayName": "Tenzin", "socials": {"instagram": "tenzin_la"}}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: profile)
    response = client.get("/api/profile/me")
    assert response.status_code == 200
    assert response.json() == profile


def test_get_my_profile_not_found(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    assert client.get("/api/profile/me").status_code == 404


def test_update_profile_all_fields_editable(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.users.update_profile", lambda uid, payload: captured.update(payload=payload)
    )
    response = client.patch(
        "/api/profile/me",
        json={
            "displayName": "Tenzin D",
            "bio": "updated",
            "dob": "1997-01-01",
            "gender": "female",
            "occupation": "nurse",
            "education": "BSc",
            "region": "Kham",
            "languages": ["bo"],
            "interests": ["thangka painting"],
            "socials": {"instagram": "new_handle", "youtube": "TenzinVlogs"},
            "location": {"lat": 46.2, "lng": 6.1},
            "preferences": {"distanceKm": 100},
        },
    )
    assert response.status_code == 200
    payload = captured["payload"]
    assert payload.dob == "1997-01-01"
    assert payload.gender == "female"
    assert payload.location.lat == 46.2
    assert payload.socials.instagram == "new_handle"
    assert payload.preferences.distanceKm == 100


def test_update_profile_rejects_blank_instagram(client):
    response = client.patch("/api/profile/me", json={"socials": {"instagram": ""}})
    assert response.status_code == 422


def test_add_photo(client, monkeypatch):
    monkeypatch.setattr("app.services.storage.blob_exists", lambda path: True)
    monkeypatch.setattr("app.services.users.add_photo", lambda uid, path, order: None)
    response = client.post(
        "/api/profile/me/photos", json={"storagePath": f"users/{TEST_UID}/photos/b.jpg"}
    )
    assert response.status_code == 200


def test_add_photo_over_cap(client, monkeypatch):
    monkeypatch.setattr("app.services.storage.blob_exists", lambda path: True)

    def over_cap(uid, path, order):
        raise ValueError("Maximum of 6 photos allowed")

    monkeypatch.setattr("app.services.users.add_photo", over_cap)
    response = client.post(
        "/api/profile/me/photos", json={"storagePath": f"users/{TEST_UID}/photos/b.jpg"}
    )
    assert response.status_code == 400


def test_delete_photo(client, monkeypatch):
    removed, deleted = {}, {}
    monkeypatch.setattr("app.services.users.remove_photo", lambda uid, path: removed.update(path=path))
    monkeypatch.setattr("app.services.storage.delete_blob", lambda path: deleted.update(path=path))
    path = f"users/{TEST_UID}/photos/b.jpg"
    response = client.delete("/api/profile/me/photos", params={"storage_path": path})
    assert response.status_code == 200
    assert removed["path"] == path
    assert deleted["path"] == path


def test_delete_photo_rejects_foreign_path(client):
    response = client.delete(
        "/api/profile/me/photos", params={"storage_path": "users/other-uid/photos/b.jpg"}
    )
    assert response.status_code == 403


def test_register_fcm_token(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.users.add_fcm_token", lambda uid, token: captured.update(t=token))
    response = client.post("/api/profile/me/fcm-tokens", json={"token": "tok-123"})
    assert response.status_code == 200
    assert captured["t"] == "tok-123"


def test_remove_fcm_token(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.users.remove_fcm_token", lambda uid, token: captured.update(t=token)
    )
    response = client.delete("/api/profile/me/fcm-tokens", params={"token": "tok-123"})
    assert response.status_code == 200
    assert captured["t"] == "tok-123"


def test_delete_my_account(client, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.users.delete_account", lambda uid: captured.update(uid=uid))
    response = client.delete("/api/profile/me")
    assert response.status_code == 200
    assert captured["uid"] == TEST_UID
