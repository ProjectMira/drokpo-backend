"""Router-level tests: each service function is mocked wholesale (same
convention as test_swipes.py), so these check routing, status-code mapping,
and account-type gating — not the post/vote logic itself (see
test_community_posts_service.py for that)."""

from app.services import communityposts as communityposts_service
from conftest import TEST_UID


def _as_community(monkeypatch):
    # /communities/me/posts* is gated by require_community_uid now.
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)


def test_create_post(client, community_post_payload, monkeypatch):
    _as_community(monkeypatch)
    monkeypatch.setattr("app.services.communityposts.create_post", lambda cid, payload: "post-1")
    response = client.post("/api/communities/me/posts", json=community_post_payload())
    assert response.status_code == 200
    assert response.json() == {"postId": "post-1"}


def test_create_post_rejects_non_community_account(client, community_post_payload, monkeypatch):
    _as_community(monkeypatch)

    def fail(cid, payload):
        raise communityposts_service.NotFoundError("Community not found")

    monkeypatch.setattr("app.services.communityposts.create_post", fail)
    response = client.post("/api/communities/me/posts", json=community_post_payload())
    assert response.status_code == 404


def test_create_post_rejects_pending_community(client, community_post_payload, monkeypatch):
    _as_community(monkeypatch)

    def fail(cid, payload):
        raise communityposts_service.NotVerifiedError("Posting unlocks once your community is verified")

    monkeypatch.setattr("app.services.communityposts.create_post", fail)
    response = client.post("/api/communities/me/posts", json=community_post_payload())
    assert response.status_code == 403
    assert "verified" in response.json()["detail"]


def test_create_post_invalid_kind_shape_maps_to_400(client, community_post_payload, monkeypatch):
    _as_community(monkeypatch)

    def fail(cid, payload):
        raise ValueError("linkUrl is required for a link post")

    monkeypatch.setattr("app.services.communityposts.create_post", fail)
    response = client.post("/api/communities/me/posts", json=community_post_payload(kind="link"))
    assert response.status_code == 400


def test_create_post_rejects_unknown_kind(client, community_post_payload, monkeypatch):
    _as_community(monkeypatch)
    response = client.post(
        "/api/communities/me/posts", json=community_post_payload(kind="tweet")
    )
    assert response.status_code == 422


def test_update_post(client, monkeypatch):
    _as_community(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "app.services.communityposts.update_post",
        lambda cid, post_id, payload: captured.update(cid=cid, post_id=post_id, payload=payload),
    )
    response = client.patch("/api/communities/me/posts/post-1", json={"active": False})
    assert response.status_code == 200
    assert captured["cid"] == TEST_UID
    assert captured["post_id"] == "post-1"
    assert captured["payload"].active is False


def test_update_post_not_found(client, monkeypatch):
    _as_community(monkeypatch)

    def fail(cid, post_id, payload):
        raise communityposts_service.PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.communityposts.update_post", fail)
    response = client.patch("/api/communities/me/posts/nope", json={"active": False})
    assert response.status_code == 404


def test_list_community_posts(client, monkeypatch):
    posts = [{"postId": "p1", "title": "Losar"}]
    monkeypatch.setattr(
        "app.services.communityposts.list_posts", lambda cid, uid, limit, before: posts
    )
    response = client.get("/api/communities/cid1/posts")
    assert response.status_code == 200
    assert response.json() == {"posts": posts}


def test_vote_on_post(client, monkeypatch):
    result = {"poll": {"options": [], "counts": {"opt1": 1}}, "myVote": "opt1"}
    monkeypatch.setattr(
        "app.services.communityposts.vote", lambda post_id, uid, option_id: result
    )
    response = client.post("/api/posts/post-1/vote", json={"optionId": "opt1"})
    assert response.status_code == 200
    assert response.json() == result


def test_vote_rejects_community_accounts(client, monkeypatch):
    # vote is gated by require_person_uid now; a community account has no
    # users/{uid} doc, which is what makes it 403.
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    response = client.post("/api/posts/post-1/vote", json={"optionId": "opt1"})
    assert response.status_code == 403


def test_vote_not_a_poll(client, monkeypatch):
    def fail(post_id, uid, option_id):
        raise communityposts_service.NotAPollError("This post is not a poll")

    monkeypatch.setattr("app.services.communityposts.vote", fail)
    response = client.post("/api/posts/post-1/vote", json={"optionId": "opt1"})
    assert response.status_code == 400


def test_vote_invalid_option(client, monkeypatch):
    def fail(post_id, uid, option_id):
        raise communityposts_service.InvalidOptionError("Unknown poll option")

    monkeypatch.setattr("app.services.communityposts.vote", fail)
    response = client.post("/api/posts/post-1/vote", json={"optionId": "bogus"})
    assert response.status_code == 400


def test_vote_post_not_found(client, monkeypatch):
    def fail(post_id, uid, option_id):
        raise communityposts_service.PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.communityposts.vote", fail)
    response = client.post("/api/posts/nope/vote", json={"optionId": "opt1"})
    assert response.status_code == 404
