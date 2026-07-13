import pytest
from conftest import TEST_UID


@pytest.fixture(autouse=True)
def no_existing_account(monkeypatch):
    # The onboarding endpoint now guards against cross-type re-creation
    # (a uid already registered as the other account type); stub both checks
    # to "doesn't exist yet" so tests below exercise the create path, same as
    # before this guard existed. Tests that specifically exercise the guard
    # override these within the test body.
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)


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
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )
    added = {}
    monkeypatch.setattr(
        "app.services.users.add_photo",
        lambda uid, path, order, url: added.update(path=path, order=order, url=url),
    )
    response = client.post(
        "/api/onboarding/photos/confirm",
        json={"storagePath": f"users/{TEST_UID}/photos/a.jpg", "order": 1},
    )
    assert response.status_code == 200
    # The resolved download URL is stored alongside the path so the app can
    # render the photo without a per-photo getDownloadURL() round-trip.
    assert added == {
        "path": f"users/{TEST_UID}/photos/a.jpg",
        "order": 1,
        "url": f"https://cdn.example/users/{TEST_UID}/photos/a.jpg",
    }


def test_confirm_photo_rejects_foreign_path(client):
    response = client.post(
        "/api/onboarding/photos/confirm", json={"storagePath": "users/other-uid/photos/a.jpg"}
    )
    assert response.status_code == 403


def test_confirm_photo_missing_blob(client, monkeypatch):
    # ensure_download_url returns None when the blob was never uploaded.
    monkeypatch.setattr("app.services.storage.ensure_download_url", lambda path: None)
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


def test_onboarding_accepts_answers_and_work(client, onboarding_payload, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.users.create_profile", lambda uid, payload: captured.update(payload=payload)
    )
    payload = onboarding_payload(
        occupation="Nurse",
        education="Bachelor's",
        answers={"teaChoice": "Butter tea", "travelledTo": " Nepal, India "},
    )
    assert client.post("/api/onboarding", json=payload).status_code == 200
    assert captured["payload"].occupation == "Nurse"
    assert captured["payload"].education == "Bachelor's"
    # Values are trimmed on the way in.
    assert captured["payload"].answers == {"teaChoice": "Butter tea", "travelledTo": "Nepal, India"}


def test_onboarding_drops_empty_answers(client, onboarding_payload, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.users.create_profile", lambda uid, payload: captured.update(payload=payload)
    )
    payload = onboarding_payload(answers={"teaChoice": "  "})
    assert client.post("/api/onboarding", json=payload).status_code == 200
    assert captured["payload"].answers == {}


def test_onboarding_rejects_oversized_answer(client, onboarding_payload):
    payload = onboarding_payload(answers={"bio2": "x" * 501})
    assert client.post("/api/onboarding", json=payload).status_code == 422


def test_onboarding_rejects_too_many_answers(client, onboarding_payload):
    payload = onboarding_payload(answers={f"q{i}": "yes" for i in range(31)})
    assert client.post("/api/onboarding", json=payload).status_code == 422
