"""The single-item GET endpoints that resolve shared deep links
(https://drokpo-backend.web.app/s/{type}/{id} → app): one user/community
card, one community post, one news story."""

import pytest

from app.services import communityposts as communityposts_service
from app.services import news as news_service

# --- GET /api/users/{uid} -----------------------------------------------------


def _no_blocks(monkeypatch):
    monkeypatch.setattr("app.routers.users.get_firestore", lambda: None)
    monkeypatch.setattr("app.services.matching._either_blocked", lambda db, a, b: False)


def test_get_user_card_returns_person(client, monkeypatch):
    _no_blocks(monkeypatch)
    monkeypatch.setattr(
        "app.services.counterparts.get_public_counterparts",
        lambda uids: {"u2": {"uid": "u2", "kind": "person", "displayName": "Dolma"}},
    )
    response = client.get("/api/users/u2")
    assert response.status_code == 200
    assert response.json() == {"uid": "u2", "kind": "person", "displayName": "Dolma"}


def test_get_user_card_returns_community_counterpart(client, monkeypatch):
    _no_blocks(monkeypatch)
    monkeypatch.setattr(
        "app.services.counterparts.get_public_counterparts",
        lambda uids: {"c1": {"uid": "c1", "kind": "community", "displayName": "TA of NY"}},
    )
    assert client.get("/api/users/c1").json()["kind"] == "community"


def test_get_user_card_404_when_missing(client, monkeypatch):
    _no_blocks(monkeypatch)
    monkeypatch.setattr("app.services.counterparts.get_public_counterparts", lambda uids: {})
    assert client.get("/api/users/ghost").status_code == 404


def test_get_user_card_404_when_blocked_either_way(client, monkeypatch):
    monkeypatch.setattr("app.routers.users.get_firestore", lambda: None)
    monkeypatch.setattr("app.services.matching._either_blocked", lambda db, a, b: True)

    def explode(uids):
        raise AssertionError("blocked lookups must not read profiles")

    monkeypatch.setattr("app.services.counterparts.get_public_counterparts", explode)
    assert client.get("/api/users/u2").status_code == 404


# --- GET /api/posts/{post_id} ---------------------------------------------------


def test_get_post_route(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.communityposts.get_post",
        lambda post_id, uid: {"postId": post_id, "kind": "announcement", "title": "Losar"},
    )
    response = client.get("/api/posts/p1")
    assert response.status_code == 200
    assert response.json()["title"] == "Losar"


def test_get_post_route_404(client, monkeypatch):
    def missing(post_id, uid):
        raise communityposts_service.PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.communityposts.get_post", missing)
    assert client.get("/api/posts/ghost").status_code == 404


class _Snap:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


class _OneDocDB:
    def __init__(self, data):
        self._snap = _Snap(data)

    def collection(self, name):
        return self

    def document(self, doc_id):
        return self

    def get(self):
        return self._snap


def test_get_post_service_hides_inactive_from_non_owner(monkeypatch):
    post = {"kind": "announcement", "title": "Draft", "active": False, "communityId": "c1"}
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: _OneDocDB(post))
    with pytest.raises(communityposts_service.PostNotFoundError):
        communityposts_service.get_post("p1", "someone-else")
    # ...but the owning community still resolves its own unpublished post.
    owned = communityposts_service.get_post("p1", "c1")
    assert owned["postId"] == "p1"
    assert owned["title"] == "Draft"


def test_get_post_service_missing_doc(monkeypatch):
    monkeypatch.setattr(communityposts_service, "get_firestore", lambda: _OneDocDB(None))
    with pytest.raises(communityposts_service.PostNotFoundError):
        communityposts_service.get_post("ghost", "anyone")


# --- GET /api/news/{news_id} ----------------------------------------------------


def test_get_news_route(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.news.get_card",
        lambda news_id: {"newsId": news_id, "title": "Headline", "gist": "Gist"},
    )
    response = client.get("/api/news/n1")
    assert response.status_code == 200
    assert response.json()["title"] == "Headline"


def test_get_news_route_404(client, monkeypatch):
    def missing(news_id):
        raise ValueError("News item not found")

    monkeypatch.setattr("app.services.news.get_card", missing)
    assert client.get("/api/news/ghost").status_code == 404


def test_get_news_card_service_serves_inactive_story(monkeypatch):
    # A story that aged out of the feed (active=False) must still resolve — a
    # link shared last week outlives the feed rotation. Private counters stay out.
    doc = {"title": "Old story", "gist": "G", "sourceUrl": "https://s.example", "active": False, "clicks": 9}
    monkeypatch.setattr(news_service, "get_firestore", lambda: _OneDocDB(doc))
    card = news_service.get_card("n1")
    assert card["newsId"] == "n1"
    assert card["title"] == "Old story"
    assert "clicks" not in card and "active" not in card


def test_get_news_card_service_missing(monkeypatch):
    monkeypatch.setattr(news_service, "get_firestore", lambda: _OneDocDB(None))
    with pytest.raises(ValueError):
        news_service.get_card("ghost")
