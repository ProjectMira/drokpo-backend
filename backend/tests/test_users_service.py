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


# --- feed distance / candidate ranking ---------------------------------------

from app.services import geo


def test_precision_matches_radius():
    assert geo.precision_for_radius(5) == 4
    assert geo.precision_for_radius(50) == 3
    assert geo.precision_for_radius(500) == 2


def test_cover_prefixes_span_neighbor_cells():
    # Delhi and Dharamshala are ~400km apart; a 500km radius must cover both.
    delhi, dharamshala = "ttngm2d", "ttwr29q"
    prefixes = geo.cover_prefixes(delhi, 500)
    assert any(dharamshala.startswith(p) for p in prefixes)
    # ...but a 50km radius must not.
    prefixes = geo.cover_prefixes(delhi, 50)
    assert not any(dharamshala.startswith(p) for p in prefixes)


class FeedStubDB:
    """Just enough Firestore for _rank_candidates: no swipes, no blocks."""

    def collection(self, name):
        return self

    def document(self, uid):
        return self

    def get_all(self, refs):
        return []


def _searcher(lat=28.7, lng=77.2, radius=50):
    return {
        "interests": ["Gorshey"],
        "location": {"lat": lat, "lng": lng},
        "preferences": {"ageMin": 18, "ageMax": 99, "distanceKm": radius},
    }


def _candidate(lat, lng, **extra):
    return {
        "displayName": "Cand",
        "dob": "1998-04-12",
        "location": {"lat": lat, "lng": lng},
        "fcmTokens": ["secret-token"],
        "preferences": {"ageMin": 20, "ageMax": 30},
        **extra,
    }


def test_rank_candidates_filters_by_distance_and_strips_private_fields(monkeypatch):
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: set())
    pool = {
        "near": _candidate(28.75, 77.25),  # ~7km away
        "far": _candidate(32.22, 76.32),  # ~400km away
    }
    result = users_service._rank_candidates(FeedStubDB(), "me", _searcher(), pool, 50, 20)
    assert [c["uid"] for c in result] == ["near"]
    assert result[0]["distanceKm"] < 50
    assert "fcmTokens" not in result[0]
    assert "preferences" not in result[0]
    assert "location" not in result[0]


def test_rank_candidates_unlimited_radius_keeps_everyone(monkeypatch):
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: set())
    pool = {"far": _candidate(32.22, 76.32)}
    result = users_service._rank_candidates(FeedStubDB(), "me", _searcher(), pool, None, 20)
    assert [c["uid"] for c in result] == ["far"]
    assert result[0]["distanceKm"] > 300


class FakeDoc:
    def __init__(self, uid, data):
        self.id = uid
        self._data = data

    def to_dict(self):
        return self._data


class GlobalOnlyDB(FeedStubDB):
    """Firestore stub whose geohash-scoped queries find nobody but whose
    unscoped query returns one far-away user — exercises the worldwide
    fallback."""

    def __init__(self, docs):
        self.docs = docs
        self.filters = []

    def where(self, *args, **kwargs):
        clone = GlobalOnlyDB(self.docs)
        clone.filters = self.filters + [args or tuple(kwargs.values())]
        return clone

    def limit(self, n):
        return self

    def stream(self):
        geohash_scoped = any("location.geohash" in str(f) for f in self.filters)
        return iter([] if geohash_scoped else self.docs)


def test_get_candidates_falls_back_worldwide_when_radius_is_empty(monkeypatch):
    db = GlobalOnlyDB([FakeDoc("far", _candidate(32.22, 76.32))])
    monkeypatch.setattr(users_service, "get_firestore", lambda: db)
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: set())
    result = users_service.get_candidates("me", _searcher(radius=50))
    assert [c["uid"] for c in result] == ["far"]


# --- community-viewer candidates --------------------------------------------


class _CommunityRef:
    """A doc ref that remembers its own id and loops any subcollection back
    to the owning stub — just enough chaining for
    db.collection(USERS).document(uid).collection("swipes").document(cand)."""

    def __init__(self, doc_id, db):
        self.id = doc_id
        self._db = db

    def collection(self, name):
        return self._db


class _SwipeSnap:
    def __init__(self, doc_id, exists):
        self.id = doc_id
        self.exists = exists


class WorldwideDB:
    """Firestore stub for get_candidates_for_community: a plain
    users-where-status-active query (no geohash filtering), plus enough
    chaining for _rank_candidates' swiped-doc batch read."""

    def __init__(self, docs, swiped=frozenset()):
        self.docs = docs
        self.swiped = set(swiped)

    def collection(self, name):
        return self

    def where(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self.docs)

    def document(self, doc_id):
        return _CommunityRef(doc_id, self)

    def get_all(self, refs):
        return [_SwipeSnap(ref.id, ref.id in self.swiped) for ref in refs]


def test_get_candidates_for_community_no_age_or_distance_filtering(monkeypatch):
    # A community's "profile" is empty (no preferences/location/interests),
    # so nobody gets excluded by age or distance — only swiped/blocked.
    db = WorldwideDB(
        [
            FakeDoc("u1", {"displayName": "Dolma", "dob": "1970-01-01"}),
            FakeDoc("u2", {"displayName": "Tenzin", "location": {"lat": 40.0, "lng": -74.0}}),
        ]
    )
    monkeypatch.setattr(users_service, "get_firestore", lambda: db)
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: set())
    result = users_service.get_candidates_for_community("c1", limit=20)
    assert {c["uid"] for c in result} == {"u1", "u2"}
    assert all("distanceKm" not in c for c in result)


def test_get_candidates_for_community_excludes_self(monkeypatch):
    db = WorldwideDB([FakeDoc("c1", {"displayName": "Self"}), FakeDoc("u2", {"displayName": "Other"})])
    monkeypatch.setattr(users_service, "get_firestore", lambda: db)
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: set())
    result = users_service.get_candidates_for_community("c1", limit=20)
    assert [c["uid"] for c in result] == ["u2"]


def test_get_candidates_for_community_excludes_swiped_and_blocked(monkeypatch):
    db = WorldwideDB(
        [
            FakeDoc("u1", {"displayName": "Swiped"}),
            FakeDoc("u2", {"displayName": "Blocked"}),
            FakeDoc("u3", {"displayName": "Fresh"}),
        ],
        swiped={"u1"},
    )
    monkeypatch.setattr(users_service, "get_firestore", lambda: db)
    monkeypatch.setattr(users_service, "_blocked_uids", lambda db, uid: {"u2"})
    result = users_service.get_candidates_for_community("c1", limit=20)
    assert [c["uid"] for c in result] == ["u3"]
