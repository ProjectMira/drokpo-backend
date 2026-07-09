"""Unit tests for the parts of the users service with real logic in them
(field flattening, geohash recompute, age filtering) using a stub Firestore."""

from app.models.user import Location, ProfileUpdate, SocialsUpdate
from app.services import users as users_service
from app.services.users import _within_age


class StubDocRef:
    def __init__(self):
        self.last_update = None

    def update(self, updates):
        self.last_update = updates


class StubDB:
    def __init__(self, doc):
        self.doc = doc

    def collection(self, name):
        return self

    def document(self, uid):
        return self.doc


def test_update_profile_flattens_and_recomputes_geohash(monkeypatch):
    doc = StubDocRef()
    monkeypatch.setattr(users_service, "get_firestore", lambda: StubDB(doc))
    users_service.update_profile(
        "u1",
        ProfileUpdate(
            gender="female",
            location=Location(lat=46.2044, lng=6.1432),
            socials=SocialsUpdate(instagram="new_handle"),
        ),
    )
    updates = doc.last_update
    assert updates["gender"] == "female"
    # geohash derived server-side, never client-supplied
    assert updates["location"]["geohash"]
    assert updates["location"]["lat"] == 46.2044
    # socials merge via dotted paths so omitted platforms are untouched
    assert updates["socials.instagram"] == "new_handle"
    assert "socials.youtube" not in updates
    assert "socials" not in updates


def test_update_profile_empty_payload_writes_nothing(monkeypatch):
    doc = StubDocRef()
    monkeypatch.setattr(users_service, "get_firestore", lambda: StubDB(doc))
    users_service.update_profile("u1", ProfileUpdate())
    assert doc.last_update is None


def test_within_age():
    assert _within_age("2000-01-01", 18, 30)
    assert not _within_age("2000-01-01", 30, 40)
    assert _within_age(None, None, None)  # no age prefs → everyone passes
    assert not _within_age(None, 18, 30)  # prefs set but no dob → excluded
    assert not _within_age("not-a-date", 18, 30)
