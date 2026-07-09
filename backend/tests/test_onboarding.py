from conftest import TEST_UID


def test_create_profile(client, onboarding_payload, monkeypatch):
    captured = {}

    def fake_create(uid, payload):
        captured["uid"] = uid
        captured["payload"] = payload

    monkeypatch.setattr("app.services.users.create_profile", fake_create)
    response = client.post("/api/onboarding", json=onboarding_payload())
    assert response.status_code == 200
    assert response.json() == {"uid": TEST_UID}
    assert captured["uid"] == TEST_UID
    assert captured["payload"].socials.instagram == "tenzin_la"
    assert captured["payload"].interests == ["hiking", "momo cooking", "gorshey"]


def test_onboarding_requires_socials(client, onboarding_payload):
    payload = onboarding_payload()
    del payload["socials"]
    assert client.post("/api/onboarding", json=payload).status_code == 422


def test_onboarding_requires_instagram(client, onboarding_payload):
    payload = onboarding_payload(socials={"youtube": "TenzinVlogs"})
    assert client.post("/api/onboarding", json=payload).status_code == 422


def test_onboarding_rejects_blank_instagram(client, onboarding_payload):
    payload = onboarding_payload(socials={"instagram": "   "})
    assert client.post("/api/onboarding", json=payload).status_code == 422


def test_onboarding_accepts_optional_socials(client, onboarding_payload, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.users.create_profile", lambda uid, payload: captured.update(payload=payload)
    )
    payload = onboarding_payload(
        socials={"instagram": "tenzin_la", "youtube": "TenzinVlogs", "tiktok": "tenzin.la"}
    )
    assert client.post("/api/onboarding", json=payload).status_code == 200
    assert captured["payload"].socials.youtube == "TenzinVlogs"
    assert captured["payload"].socials.facebook is None


def test_onboarding_gender_optional(client, onboarding_payload, monkeypatch):
    monkeypatch.setattr("app.services.users.create_profile", lambda uid, payload: None)
    payload = onboarding_payload()
    del payload["gender"]
    assert client.post("/api/onboarding", json=payload).status_code == 200


def test_confirm_photo(client, monkeypatch):
    monkeypatch.setattr("app.services.storage.blob_exists", lambda path: True)
    added = {}
    monkeypatch.setattr(
        "app.services.users.add_photo", lambda uid, path, order: added.update(path=path, order=order)
    )
    response = client.post(
        "/api/onboarding/photos/confirm",
        json={"storagePath": f"users/{TEST_UID}/photos/a.jpg", "order": 1},
    )
    assert response.status_code == 200
    assert added == {"path": f"users/{TEST_UID}/photos/a.jpg", "order": 1}


def test_confirm_photo_rejects_foreign_path(client):
    response = client.post(
        "/api/onboarding/photos/confirm", json={"storagePath": "users/other-uid/photos/a.jpg"}
    )
    assert response.status_code == 403


def test_confirm_photo_missing_blob(client, monkeypatch):
    monkeypatch.setattr("app.services.storage.blob_exists", lambda path: False)
    response = client.post(
        "/api/onboarding/photos/confirm", json={"storagePath": f"users/{TEST_UID}/photos/a.jpg"}
    )
    assert response.status_code == 400


def test_complete_onboarding(client, monkeypatch):
    monkeypatch.setattr("app.services.users.complete_onboarding", lambda uid: None)
    assert client.post("/api/onboarding/complete").json() == {"ok": True}


def test_complete_onboarding_requires_photo(client, monkeypatch):
    def fail(uid):
        raise ValueError("At least one photo is required to complete onboarding")

    monkeypatch.setattr("app.services.users.complete_onboarding", fail)
    response = client.post("/api/onboarding/complete")
    assert response.status_code == 400
    assert "photo" in response.json()["detail"]
