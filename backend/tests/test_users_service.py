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


class StubSnap:
    exists = True

    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class StubReorderRef:
    def __init__(self, photos):
        self._photos = photos
        self.last_update = None

    def get(self):
        return StubSnap({"photos": self._photos})

    def update(self, updates):
        self.last_update = updates


class StubReorderDB:
    def __init__(self, ref):
        self.ref = ref

    def collection(self, name):
        return self

    def document(self, uid):
        return self.ref


def test_reorder_photos_rewrites_order_and_preserves_keys(monkeypatch):
    photos = [
        {"storagePath": "a.jpg", "order": 0, "url": "https://a"},
        {"storagePath": "b.jpg", "order": 1, "url": "https://b"},
    ]
    ref = StubReorderRef(photos)
    monkeypatch.setattr(users_service, "get_firestore", lambda: StubReorderDB(ref))

    users_service.reorder_photos("u1", ["b.jpg", "a.jpg"])

    reordered = ref.last_update["photos"]
    assert [p["storagePath"] for p in reordered] == ["b.jpg", "a.jpg"]
    assert [p["order"] for p in reordered] == [0, 1]
    # per-photo keys (like the resolved url) survive the rewrite
    assert reordered[0]["url"] == "https://b"
    assert reordered[1]["url"] == "https://a"


def test_reorder_photos_rejects_mismatched_set(monkeypatch):
    photos = [{"storagePath": "a.jpg", "order": 0}, {"storagePath": "b.jpg", "order": 1}]
    ref = StubReorderRef(photos)
    monkeypatch.setattr(users_service, "get_firestore", lambda: StubReorderDB(ref))

    import pytest

    with pytest.raises(ValueError):
        users_service.reorder_photos("u1", ["a.jpg", "c.jpg"])
    assert ref.last_update is None


def test_within_age():
    assert _within_age("2000-01-01", 18, 30)
    assert not _within_age("2000-01-01", 30, 40)
    assert _within_age(None, None, None)  # no age prefs → everyone passes
    assert not _within_age(None, 18, 30)  # prefs set but no dob → excluded
    assert not _within_age("not-a-date", 18, 30)
