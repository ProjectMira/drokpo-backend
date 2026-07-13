import pytest

from app.services import news as news_service


@pytest.fixture(autouse=True)
def clear_news_cache():
    # list_active memoizes for 60s in-process; isolate tests from each other.
    news_service._cache.update(expires=0.0, limit=None, news=None)
    yield
    news_service._cache.update(expires=0.0, limit=None, news=None)


def test_record_click_event(client, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "app.services.news.record_event",
        lambda news_id, event: recorded.update(news_id=news_id, event=event),
    )
    response = client.post("/api/news/n1/events", json={"event": "click"})
    assert response.status_code == 200
    assert recorded == {"news_id": "n1", "event": "click"}


def test_record_impression_event(client, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "app.services.news.record_event",
        lambda news_id, event: recorded.update(news_id=news_id, event=event),
    )
    assert client.post("/api/news/n1/events", json={"event": "impression"}).status_code == 200
    assert recorded["event"] == "impression"


def test_unknown_event_rejected(client):
    assert client.post("/api/news/n1/events", json={"event": "view"}).status_code == 422


def test_missing_news_is_404(client, monkeypatch):
    def raise_missing(news_id, event):
        raise ValueError("News item not found")

    monkeypatch.setattr("app.services.news.record_event", raise_missing)
    assert client.post("/api/news/nope/events", json={"event": "click"}).status_code == 404


def test_news_events_require_auth(anon_client):
    assert anon_client.post("/api/news/n1/events", json={"event": "click"}).status_code == 422


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


def test_list_active_shapes_and_orders_newest_first(monkeypatch):
    # order is written as -publishedEpoch by the news-digest skill, so a plain
    # ascending sort surfaces the newest article first.
    docs = [
        FakeDoc(
            "older",
            {
                "title": "Older story",
                "gist": "Something happened yesterday.",
                "sourceUrl": "https://src.example/older",
                "sourceName": "Phayul",
                "active": True,
                "order": -1000,
                "clicks": 9,
            },
        ),
        FakeDoc(
            "newer",
            {
                "title": "Newer story",
                "gist": "Something happened today.",
                "sourceUrl": "https://src.example/newer",
                "sourceName": "Phayul",
                "active": True,
                "order": -2000,
                "summary": "A longer account of today's events.",
                "imageUrl": "https://img.example/newer.jpg",
                "publishedAt": "2026-07-13",
            },
        ),
        FakeDoc("broken", {"title": "No source url or gist", "active": True}),
    ]
    monkeypatch.setattr("app.services.news.get_firestore", lambda: FakeDB(docs))
    news = news_service.list_active()
    assert [n["newsId"] for n in news] == ["newer", "older"]
    assert news[0] == {
        "newsId": "newer",
        "title": "Newer story",
        "gist": "Something happened today.",
        "summary": "A longer account of today's events.",
        "sourceUrl": "https://src.example/newer",
        "sourceName": "Phayul",
        "imageUrl": "https://img.example/newer.jpg",
        "publishedAt": "2026-07-13",
    }
    # Internal counters never leak to the client.
    assert "clicks" not in news[1]


def test_list_active_skips_docs_missing_required_fields(monkeypatch):
    docs = [
        FakeDoc("no-gist", {"title": "x", "sourceUrl": "https://a", "active": True}),
        FakeDoc("no-title", {"gist": "x", "sourceUrl": "https://a", "active": True}),
        FakeDoc("no-source", {"title": "x", "gist": "x", "active": True}),
    ]
    monkeypatch.setattr("app.services.news.get_firestore", lambda: FakeDB(docs))
    assert news_service.list_active() == []


def test_record_event_validates_kind():
    with pytest.raises(ValueError):
        news_service.record_event("n1", "hover")


def test_list_active_is_cached(monkeypatch):
    calls = {"count": 0}

    def counting_db():
        calls["count"] += 1
        return FakeDB(
            [FakeDoc("n1", {"title": "x", "gist": "x", "sourceUrl": "https://a", "active": True})]
        )

    monkeypatch.setattr("app.services.news.get_firestore", counting_db)
    first = news_service.list_active()
    second = news_service.list_active()
    assert first == second
    # Second call inside the TTL is served from memory, not Firestore.
    assert calls["count"] == 1
