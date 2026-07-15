"""Unit tests for the role-aware counterpart join (app/services/counterparts.py)
used by swipes/matches to resolve "the other uid" to either a person profile
or a community's public card, tagged with an additive `kind` discriminator."""

from app.services import counterparts as counterparts_service


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
    def __init__(self, community_docs):
        self.community_docs = community_docs

    def collection(self, name):
        assert name == "communities"
        return StubCollection()

    def get_all(self, refs):
        return [StubSnap(ref.id, self.community_docs.get(ref.id)) for ref in refs]


def test_get_public_counterparts_tags_persons(monkeypatch):
    monkeypatch.setattr(
        "app.services.users.get_public_profiles",
        lambda uids: {"p1": {"uid": "p1", "displayName": "Dolma"}},
    )
    monkeypatch.setattr(counterparts_service, "get_firestore", lambda: StubDB({}))
    result = counterparts_service.get_public_counterparts(["p1"])
    assert result["p1"]["kind"] == "person"
    assert result["p1"]["displayName"] == "Dolma"


def test_get_public_counterparts_maps_community_when_no_person_profile(monkeypatch):
    monkeypatch.setattr("app.services.users.get_public_profiles", lambda uids: {})
    community = {
        "name": "Tibetan Association of NY",
        "description": "Serving Tibetans in the NY area",
        "address": {"city": "New York", "country": "USA"},
        "photos": [{"url": "https://cdn.example/logo.jpg"}],
        "socials": {"instagram": "tany"},
        "verification": "verified",
        "memberCount": 42,
        "email": "hello@example.org",
        "fcmTokens": ["should-never-leak"],
    }
    monkeypatch.setattr(counterparts_service, "get_firestore", lambda: StubDB({"c1": community}))
    result = counterparts_service.get_public_counterparts(["c1"])
    card = result["c1"]
    assert card["kind"] == "community"
    assert card["uid"] == "c1"
    assert card["displayName"] == "Tibetan Association of NY"
    assert card["bio"] == "Serving Tibetans in the NY area"
    assert card["region"] == "New York, USA"
    assert card["photos"] == community["photos"]
    assert card["verification"] == "verified"
    assert card["memberCount"] == 42
    assert "email" not in card
    assert "fcmTokens" not in card


def test_get_public_counterparts_region_handles_missing_address_parts(monkeypatch):
    monkeypatch.setattr("app.services.users.get_public_profiles", lambda uids: {})
    community = {"name": "No Address Community", "address": {}}
    monkeypatch.setattr(counterparts_service, "get_firestore", lambda: StubDB({"c1": community}))
    assert counterparts_service.get_public_counterparts(["c1"])["c1"]["region"] is None


def test_get_public_counterparts_drops_uid_in_neither_collection(monkeypatch):
    monkeypatch.setattr("app.services.users.get_public_profiles", lambda uids: {})
    monkeypatch.setattr(counterparts_service, "get_firestore", lambda: StubDB({}))
    assert counterparts_service.get_public_counterparts(["gone"]) == {}


def test_get_public_counterparts_empty_input_never_touches_firestore(monkeypatch):
    def explode():
        raise AssertionError("get_firestore must not be called")

    monkeypatch.setattr(counterparts_service, "get_firestore", explode)
    assert counterparts_service.get_public_counterparts([]) == {}


def test_get_public_counterparts_dedupes_input(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "app.services.users.get_public_profiles",
        lambda uids: seen.update(uids=list(uids)) or {},
    )
    monkeypatch.setattr(counterparts_service, "get_firestore", lambda: StubDB({}))
    counterparts_service.get_public_counterparts(["p1", "p1", "p1"])
    assert seen["uids"] == ["p1"]
