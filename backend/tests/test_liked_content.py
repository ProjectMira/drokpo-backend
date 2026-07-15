"""Router tests for liked news / liked posts / merged liked-content list /
communities home. Service internals are exercised with monkeypatched service
functions, matching the conventions of the other router test files."""


def test_like_news_returns_snapshot(client, monkeypatch):
    captured = {}
    snapshot = {"newsId": "n1", "title": "Headline", "gist": "G", "sourceUrl": "https://s.example"}
    monkeypatch.setattr(
        "app.services.news.like", lambda uid, news_id: captured.update(uid=uid, news_id=news_id) or snapshot
    )
    response = client.put("/api/news/n1/like")
    assert response.status_code == 200
    assert response.json() == snapshot
    assert captured == {"uid": "test-uid", "news_id": "n1"}


def test_like_missing_news_404(client, monkeypatch):
    def boom(uid, news_id):
        raise ValueError("News item not found")

    monkeypatch.setattr("app.services.news.like", boom)
    assert client.put("/api/news/gone/like").status_code == 404


def test_unlike_news_idempotent(client, monkeypatch):
    monkeypatch.setattr("app.services.news.unlike", lambda uid, news_id: None)
    assert client.delete("/api/news/n1/like").status_code == 200


def test_like_news_allows_community_account(client, monkeypatch):
    # Community accounts can save Discover content (news) just like persons.
    snapshot = {"newsId": "n1", "title": "Headline", "gist": "G", "sourceUrl": "https://s.example"}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr("app.services.news.like", lambda uid, news_id: snapshot)
    assert client.put("/api/news/n1/like").status_code == 200


def test_like_news_rejects_neither_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    assert client.put("/api/news/n1/like").status_code == 403


def test_like_post_and_unlike(client, monkeypatch):
    snapshot = {"postId": "p1", "kind": "announcement", "title": "Losar"}
    monkeypatch.setattr("app.services.communityposts.like", lambda uid, post_id: snapshot)
    monkeypatch.setattr("app.services.communityposts.unlike", lambda uid, post_id: None)
    assert client.put("/api/posts/p1/like").json() == snapshot
    assert client.delete("/api/posts/p1/like").status_code == 200


def test_like_missing_post_404(client, monkeypatch):
    from app.services.communityposts import PostNotFoundError

    def boom(uid, post_id):
        raise PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.communityposts.like", boom)
    assert client.put("/api/posts/gone/like").status_code == 404


def test_liked_content_merges_newest_first(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.news.list_liked",
        lambda uid, limit: [
            {"newsId": "n1", "title": "Old story", "likedAt": "2026-07-10T10:00:00+00:00"},
            {"newsId": "n2", "title": "New story", "likedAt": "2026-07-14T10:00:00+00:00"},
        ],
    )
    monkeypatch.setattr(
        "app.services.communityposts.list_liked",
        lambda uid, limit: [
            {"postId": "p1", "title": "Middle post", "likedAt": "2026-07-12T10:00:00+00:00"},
        ],
    )
    body = client.get("/api/likes/content").json()
    assert [item["type"] for item in body["items"]] == ["news", "communityPost", "news"]
    assert [item["data"].get("newsId") or item["data"].get("postId") for item in body["items"]] == [
        "n2",
        "p1",
        "n1",
    ]


def test_communities_home_bundles_rail_and_items(client, monkeypatch):
    communities = [{"uid": "c1", "name": "TANY"}]
    posts = [{"postId": f"p{i}", "kind": "announcement", "title": f"T{i}"} for i in range(5)]
    ads = [{"adId": "ad1", "title": "Bojang", "linkUrl": "https://bojang.in"}]
    monkeypatch.setattr("app.services.communities.list_my_communities", lambda uid: communities)
    monkeypatch.setattr("app.services.communityposts.list_feed_for_member", lambda uid, limit: posts)
    monkeypatch.setattr("app.services.ads.list_active", lambda: ads)
    body = client.get("/api/communities/home").json()
    assert body["communities"] == communities
    types = [item["type"] for item in body["items"]]
    assert types == ["communityPost"] * 4 + ["ad"] + ["communityPost"]


def test_communities_home_requires_person_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    assert client.get("/api/communities/home").status_code == 403
