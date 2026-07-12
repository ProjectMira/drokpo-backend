import pytest

from app.services import ads as ads_service


@pytest.fixture(autouse=True)
def clear_ads_cache():
    # list_active memoizes for 60s in-process; isolate tests from each other.
    ads_service._cache.update(expires=0.0, limit=None, ads=None)
    yield
    ads_service._cache.update(expires=0.0, limit=None, ads=None)


def test_record_click_event(client, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "app.services.ads.record_event", lambda ad_id, event: recorded.update(ad_id=ad_id, event=event)
    )
    response = client.post("/api/ads/ad1/events", json={"event": "click"})
    assert response.status_code == 200
    assert recorded == {"ad_id": "ad1", "event": "click"}


def test_record_impression_event(client, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "app.services.ads.record_event", lambda ad_id, event: recorded.update(ad_id=ad_id, event=event)
    )
    assert client.post("/api/ads/ad1/events", json={"event": "impression"}).status_code == 200
    assert recorded["event"] == "impression"


def test_unknown_event_rejected(client):
    assert client.post("/api/ads/ad1/events", json={"event": "view"}).status_code == 422


def test_missing_ad_is_404(client, monkeypatch):
    def raise_missing(ad_id, event):
        raise ValueError("Ad not found")

    monkeypatch.setattr("app.services.ads.record_event", raise_missing)
    assert client.post("/api/ads/nope/events", json={"event": "click"}).status_code == 404


def test_ad_events_require_auth(anon_client):
    assert anon_client.post("/api/ads/ad1/events", json={"event": "click"}).status_code == 422


class FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class FakeDB:
    def __init__(self, docs):
        self._docs = docs

    def collection(self, name):
        return FakeQuery(self._docs)


def test_list_active_shapes_and_orders(monkeypatch):
    docs = [
        FakeDoc("b", {"title": "Second", "linkUrl": "https://b", "order": 2, "active": True, "clicks": 9}),
        FakeDoc("a", {"title": "First", "linkUrl": "https://a", "order": 1, "active": True,
                      "body": "Fresh momos", "imageUrl": "https://img", "ctaLabel": "Order now"}),
        FakeDoc("broken", {"title": "No link", "active": True}),
    ]
    monkeypatch.setattr("app.services.ads.get_firestore", lambda: FakeDB(docs))
    ads = ads_service.list_active()
    assert [ad["adId"] for ad in ads] == ["a", "b"]
    assert ads[0] == {
        "adId": "a",
        "title": "First",
        "body": "Fresh momos",
        "linkUrl": "https://a",
        "ctaLabel": "Order now",
        "imageUrl": "https://img",
    }
    # Internal counters never leak to the client.
    assert "clicks" not in ads[1]


def test_record_event_validates_kind():
    with pytest.raises(ValueError):
        ads_service.record_event("ad1", "hover")


def test_list_active_is_cached(monkeypatch):
    calls = {"count": 0}

    def counting_db():
        calls["count"] += 1
        return FakeDB([FakeDoc("a", {"title": "Ad", "linkUrl": "https://a", "active": True})])

    monkeypatch.setattr("app.services.ads.get_firestore", counting_db)
    first = ads_service.list_active()
    second = ads_service.list_active()
    assert first == second
    # Second call inside the TTL is served from memory, not Firestore.
    assert calls["count"] == 1


def test_list_active_resolves_photo_urls(monkeypatch):
    docs = [
        FakeDoc(
            "a",
            {
                "title": "Momo House",
                "linkUrl": "https://momo",
                "active": True,
                "photos": [{"storagePath": "ads/momo.jpg"}, {"url": "https://already"}],
            },
        )
    ]
    monkeypatch.setattr("app.services.ads.get_firestore", lambda: FakeDB(docs))
    monkeypatch.setattr(
        "app.services.storage.ensure_download_url", lambda path: f"https://cdn.example/{path}"
    )
    ads = ads_service.list_active()
    assert ads[0]["photos"][0] == {
        "storagePath": "ads/momo.jpg",
        "url": "https://cdn.example/ads/momo.jpg",
    }
    # Photos that already carry a url are passed through untouched.
    assert ads[0]["photos"][1] == {"url": "https://already"}


def test_list_active_survives_broken_creative(monkeypatch):
    docs = [
        FakeDoc(
            "a",
            {
                "title": "Ad",
                "linkUrl": "https://a",
                "active": True,
                "photos": [{"storagePath": "ads/gone.jpg"}],
            },
        )
    ]
    monkeypatch.setattr("app.services.ads.get_firestore", lambda: FakeDB(docs))

    def boom(path):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr("app.services.storage.ensure_download_url", boom)
    ads = ads_service.list_active()
    # The ad still serves; the photo just lacks a resolved url.
    assert ads[0]["photos"] == [{"storagePath": "ads/gone.jpg"}]
