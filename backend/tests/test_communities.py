import pytest
from conftest import TEST_UID


@pytest.fixture(autouse=True)
def default_is_community(monkeypatch):
    """Most tests below exercise /communities/me* endpoints, now gated by
    require_community_uid — default TEST_UID to "is a community" so they
    don't all need to repeat this setup. Tests for the opposite (onboarding
    conflicts) override both mocks explicitly, which wins for that test."""
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)


def test_create_community(client, community_onboarding_payload, monkeypatch):
    captured = {}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    monkeypatch.setattr(
        "app.services.communities.create_community",
        lambda uid, payload: captured.update(uid=uid, payload=payload),
    )
    response = client.post("/api/communities/onboarding", json=community_onboarding_payload())
    assert response.status_code == 200
    assert response.json() == {"uid": TEST_UID}
    assert captured["uid"] == TEST_UID
    assert captured["payload"].name == "Tibetan Association of NY"
    assert captured["payload"].contactPerson.name == "Dolma"


def test_create_community_rejects_non_https_website(client, community_onboarding_payload):
    payload = community_onboarding_payload(website="http://example.org")
    assert client.post("/api/communities/onboarding", json=payload).status_code == 422


def test_create_community_socials_has_no_required_handle(client, community_onboarding_payload, monkeypatch):
    # Unlike a person's onboarding (where Instagram is required), a community
    # may supply a partial socials object with no handle at all.
    captured = {}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    monkeypatch.setattr(
        "app.services.communities.create_community",
        lambda uid, payload: captured.update(payload=payload),
    )
    payload = community_onboarding_payload(socials={"facebook": "tany.official"})
    response = client.post("/api/communities/onboarding", json=payload)
    assert response.status_code == 200
    assert captured["payload"].socials.facebook == "tany.official"
    assert captured["payload"].socials.instagram is None


def test_create_community_requires_email(client, community_onboarding_payload):
    payload = community_onboarding_payload()
    del payload["email"]
    assert client.post("/api/communities/onboarding", json=payload).status_code == 422


def test_create_community_rejects_invalid_email(client, community_onboarding_payload):
    payload = community_onboarding_payload(email="not-an-email")
    assert client.post("/api/communities/onboarding", json=payload).status_code == 422


def test_update_community_rejects_blank_email(client):
    response = client.patch("/api/communities/me", json={"email": ""})
    assert response.status_code == 422


def test_create_community_requires_contact_name(client, community_onboarding_payload):
    payload = community_onboarding_payload(contactPerson={"role": "Coordinator"})
    assert client.post("/api/communities/onboarding", json=payload).status_code == 422


def test_create_community_requires_city_and_country(client, community_onboarding_payload):
    payload = community_onboarding_payload(address={"line1": "123 Main St"})
    assert client.post("/api/communities/onboarding", json=payload).status_code == 422


def test_create_community_conflicts_with_existing_person(client, community_onboarding_payload, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: {"uid": uid})
    response = client.post("/api/communities/onboarding", json=community_onboarding_payload())
    assert response.status_code == 409


def test_create_community_conflicts_with_existing_community(
    client, community_onboarding_payload, monkeypatch
):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    response = client.post("/api/communities/onboarding", json=community_onboarding_payload())
    assert response.status_code == 409


def test_onboarding_rejects_when_community_exists(client, onboarding_payload, monkeypatch):
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    response = client.post("/api/onboarding", json=onboarding_payload())
    assert response.status_code == 409


def test_get_my_community(client, monkeypatch):
    community = {"uid": TEST_UID, "name": "TANY", "verification": "pending"}
    monkeypatch.setattr("app.services.communities.get_community", lambda uid: community)
    response = client.get("/api/communities/me")
    assert response.status_code == 200
    assert response.json() == community


def test_get_my_community_not_found(client, monkeypatch):
    monkeypatch.setattr("app.services.communities.get_community", lambda uid: None)
    assert client.get("/api/communities/me").status_code == 404


def test_update_community_pending_still_editable(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.update_community",
        lambda uid, payload: captured.update(payload=payload),
    )
    response = client.patch(
        "/api/communities/me",
        json={
            "name": "New Name",
            "contactPerson": {"phone": "+1-555-0199"},
            "address": {"city": "Queens"},
            "socials": {"instagram": "tany_official"},
        },
    )
    assert response.status_code == 200
    payload = captured["payload"]
    assert payload.name == "New Name"
    assert payload.contactPerson.phone == "+1-555-0199"
    assert payload.address.city == "Queens"
    assert payload.socials.instagram == "tany_official"


def test_update_community_rejects_non_https_website(client):
    response = client.patch("/api/communities/me", json={"website": "ftp://example.org"})
    assert response.status_code == 422


def test_add_community_photo(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )
    monkeypatch.setattr("app.services.communities.add_photo", lambda uid, path, order, url: None)
    response = client.post(
        "/api/communities/me/photos", json={"storagePath": f"communities/{TEST_UID}/photos/logo.jpg"}
    )
    assert response.status_code == 200


def test_add_community_photo_rejects_foreign_path(client):
    response = client.post(
        "/api/communities/me/photos", json={"storagePath": "communities/other-uid/photos/logo.jpg"}
    )
    assert response.status_code == 403


def test_add_community_photo_over_cap(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )

    def over_cap(uid, path, order, url):
        raise ValueError("Maximum of 6 photos allowed")

    monkeypatch.setattr("app.services.communities.add_photo", over_cap)
    response = client.post(
        "/api/communities/me/photos", json={"storagePath": f"communities/{TEST_UID}/photos/logo.jpg"}
    )
    assert response.status_code == 400


def test_delete_community_photo(client, monkeypatch):
    removed, deleted = {}, {}
    monkeypatch.setattr(
        "app.services.communities.remove_photo", lambda uid, path: removed.update(path=path)
    )
    monkeypatch.setattr("app.services.storage.delete_blob", lambda path: deleted.update(path=path))
    path = f"communities/{TEST_UID}/photos/logo.jpg"
    response = client.delete("/api/communities/me/photos", params={"storage_path": path})
    assert response.status_code == 200
    assert removed["path"] == path
    assert deleted["path"] == path


def test_reorder_community_photos(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.reorder_photos",
        lambda uid, paths: captured.update(uid=uid, paths=paths),
    )
    paths = [f"communities/{TEST_UID}/photos/b.jpg", f"communities/{TEST_UID}/photos/a.jpg"]
    response = client.patch("/api/communities/me/photos/order", json={"storagePaths": paths})
    assert response.status_code == 200
    assert captured["paths"] == paths


def test_delete_my_community(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.delete_community", lambda uid: captured.update(uid=uid)
    )
    response = client.delete("/api/communities/me")
    assert response.status_code == 200
    assert captured["uid"] == TEST_UID


# ---------------------------------------------------------------------------
# update_community clearing semantics — "" means "delete this optional field"
# ---------------------------------------------------------------------------


class _CapturingDb:
    """Minimal Firestore stand-in: records the update() payload."""

    def __init__(self, captured):
        self._captured = captured

    def collection(self, name):
        return self

    def document(self, uid):
        return self

    def update(self, updates):
        self._captured["updates"] = updates


def test_update_community_empty_string_clears_optional_fields(monkeypatch):
    from firebase_admin import firestore

    from app.models.community import CommunityUpdate
    from app.services import communities as communities_service

    captured = {}
    monkeypatch.setattr(
        "app.services.communities.get_firestore", lambda: _CapturingDb(captured)
    )
    payload = CommunityUpdate.model_validate(
        {
            "website": "",
            "phone": "",
            "contactPerson": {"role": "", "phone": "+1-555-0100"},
            "address": {"line1": "", "city": ""},
            "socials": {"youtube": ""},
        }
    )
    communities_service.update_community("cid1", payload)
    updates = captured["updates"]
    assert updates["website"] is firestore.DELETE_FIELD
    assert updates["phone"] is firestore.DELETE_FIELD
    assert updates["contactPerson.role"] is firestore.DELETE_FIELD
    assert updates["address.line1"] is firestore.DELETE_FIELD
    assert updates["socials.youtube"] is firestore.DELETE_FIELD
    # Real values still pass through alongside clears.
    assert updates["contactPerson.phone"] == "+1-555-0100"
    # Required-at-onboarding fields are NOT clearable — a stray "" is dropped.
    assert "address.city" not in updates
    assert "updatedAt" in updates


def test_update_community_all_blank_is_a_noop_for_required_fields(monkeypatch):
    from app.models.community import CommunityUpdate
    from app.services import communities as communities_service

    captured = {}
    monkeypatch.setattr(
        "app.services.communities.get_firestore", lambda: _CapturingDb(captured)
    )
    communities_service.update_community(
        "cid1", CommunityUpdate.model_validate({"address": {"city": "", "country": ""}})
    )
    # Nothing clearable was sent → no Firestore write at all.
    assert captured == {}


def test_update_community_accepts_empty_website_via_router(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communities.update_community",
        lambda uid, payload: captured.update(payload=payload),
    )
    response = client.patch("/api/communities/me", json={"website": ""})
    assert response.status_code == 200
    assert captured["payload"].website == ""


# ---------------------------------------------------------------------------
# Open registration: list_directory / get_community_card no longer filter by
# verification — verification is a badge (memberCount seal), not a gate.
# ---------------------------------------------------------------------------


class _DirDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class _DirRef:
    """A doc ref that loops any subcollection back to an empty query — enough
    for _joined_set's users/{uid}/memberships/{cid} batch-read chain, which
    these tests want to resolve to "not joined" (empty)."""

    def collection(self, name):
        return _DirQuery([])

    def get(self):
        return self


class _DirQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        raise AssertionError("list_directory must not filter by verification")

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)

    def document(self, doc_id):
        return _DirRef()


class _DirDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return _DirQuery(self._docs)

    def document(self, doc_id):
        return _DirRef()

    def get_all(self, refs):
        return []


def test_list_directory_includes_unverified_communities(monkeypatch):
    from app.services import communities as communities_service

    docs = [
        _DirDoc("c1", {"name": "Verified Co", "verification": "verified", "memberCount": 5}),
        _DirDoc("c2", {"name": "Pending Co", "verification": "pending", "memberCount": 1}),
    ]
    monkeypatch.setattr(communities_service, "get_firestore", lambda: _DirDB(docs))
    result = communities_service.list_directory("viewer-uid", limit=50)
    assert {c["uid"] for c in result} == {"c1", "c2"}


def test_get_community_card_returns_unverified_community(monkeypatch):
    from app.services import communities as communities_service

    monkeypatch.setattr(
        communities_service,
        "get_community",
        lambda cid: {"uid": cid, "name": "Pending Co", "verification": "pending"},
    )
    monkeypatch.setattr(communities_service, "_joined_set", lambda db, uid, cids: set())
    monkeypatch.setattr(communities_service, "get_firestore", lambda: _DirDB([]))
    card = communities_service.get_community_card("viewer-uid", "c2")
    assert card is not None
    assert card["name"] == "Pending Co"
    assert card["verification"] == "pending"


def test_get_community_card_still_404s_for_missing_community(monkeypatch):
    from app.services import communities as communities_service

    monkeypatch.setattr(communities_service, "get_community", lambda cid: None)
    assert communities_service.get_community_card("viewer-uid", "gone") is None
