"""RSVP coverage: the transaction logic itself (stubbed Firestore, calling
the undecorated function directly — google-cloud-firestore's @transactional
stores the original callable on `.to_wrap`, so this exercises the real
count/no-op math without needing a live Firestore transaction), plus
router-level gating tests mirroring test_community_posts.py's convention."""

import pytest

from app.services import communityposts as communityposts_service
from conftest import TEST_UID


# --- _rsvp_transaction() — real count/no-op logic, stubbed Firestore -------


class StubSnap:
    def __init__(self, data, exists=True):
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


class StubRef:
    def __init__(self, snap):
        self._snap = snap

    def get(self, transaction=None):
        return self._snap


class StubSubcollection:
    def __init__(self, ref):
        self._ref = ref

    def document(self, uid):
        return self._ref


class StubPostRef:
    def __init__(self, post_snap, rsvp_ref):
        self._post_snap = post_snap
        self._rsvp_ref = rsvp_ref

    def get(self, transaction=None):
        return self._post_snap

    def collection(self, name):
        return StubSubcollection(self._rsvp_ref)


class StubDB:
    def __init__(self, post_ref):
        self._post_ref = post_ref

    def collection(self, name):
        return self

    def document(self, post_id):
        return self._post_ref


class FakeTransaction:
    """Records writes; no transaction lifecycle (_begin/_commit) needed since
    we call the undecorated function directly, bypassing _Transactional."""

    def __init__(self):
        self.sets = []
        self.deletes = []
        self.updates = []

    def set(self, ref, data):
        self.sets.append((ref, data))

    def delete(self, ref):
        self.deletes.append(ref)

    def update(self, ref, data):
        self.updates.append((ref, data))


def _run_rsvp(post_data, rsvp_exists, going, post_exists=True):
    post_snap = StubSnap(post_data, exists=post_exists)
    rsvp_snap = StubSnap({"createdAt": "sometime"}, exists=rsvp_exists)
    rsvp_ref = StubRef(rsvp_snap)
    post_ref = StubPostRef(post_snap, rsvp_ref)
    db = StubDB(post_ref)
    transaction = FakeTransaction()
    raw = communityposts_service._rsvp_transaction.to_wrap
    count = raw(transaction, db, "post-1", TEST_UID, going)
    return count, transaction


def test_rsvp_new_attendee_increments_count():
    count, txn = _run_rsvp({"kind": "event", "active": True, "attendeeCount": 2}, rsvp_exists=False, going=True)
    assert count == 3
    assert len(txn.sets) == 1
    assert txn.updates == [(txn.updates[0][0], {"attendeeCount": 3})]


def test_rsvp_cancel_decrements_count():
    count, txn = _run_rsvp({"kind": "event", "active": True, "attendeeCount": 3}, rsvp_exists=True, going=False)
    assert count == 2
    assert len(txn.deletes) == 1
    assert txn.updates == [(txn.updates[0][0], {"attendeeCount": 2})]


def test_rsvp_duplicate_going_is_noop():
    count, txn = _run_rsvp({"kind": "event", "active": True, "attendeeCount": 5}, rsvp_exists=True, going=True)
    assert count == 5
    assert txn.sets == [] and txn.deletes == [] and txn.updates == []


def test_rsvp_double_cancel_is_noop():
    count, txn = _run_rsvp({"kind": "event", "active": True, "attendeeCount": 0}, rsvp_exists=False, going=False)
    assert count == 0
    assert txn.sets == [] and txn.deletes == [] and txn.updates == []


def test_rsvp_count_never_goes_negative():
    # Defends against a count that's already out of sync somehow — cancelling
    # never drives attendeeCount below zero.
    count, txn = _run_rsvp({"kind": "event", "active": True, "attendeeCount": 0}, rsvp_exists=True, going=False)
    assert count == 0


def test_rsvp_rejects_non_event_post():
    with pytest.raises(communityposts_service.NotAnEventError):
        _run_rsvp({"kind": "poll", "active": True}, rsvp_exists=False, going=True)


def test_rsvp_rejects_inactive_post():
    with pytest.raises(communityposts_service.PostNotFoundError):
        _run_rsvp({"kind": "event", "active": False}, rsvp_exists=False, going=True)


def test_rsvp_rejects_missing_post():
    with pytest.raises(communityposts_service.PostNotFoundError):
        _run_rsvp({}, rsvp_exists=False, going=True, post_exists=False)


# --- router-level: gating + status-code mapping (service mocked wholesale) -


def test_rsvp_to_event(client, monkeypatch):
    result = {"attendeeCount": 4, "going": True}
    monkeypatch.setattr(
        "app.services.communityposts.rsvp", lambda post_id, uid, going: result
    )
    response = client.post("/api/posts/post-1/rsvp")
    assert response.status_code == 200
    assert response.json() == result


def test_rsvp_allows_community_accounts(client, monkeypatch):
    # RSVP is gated by require_account_uid — community accounts can save/RSVP
    # to Discover content just like persons (they just can't join communities).
    result = {"attendeeCount": 4, "going": True}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr("app.services.communityposts.rsvp", lambda post_id, uid, going: result)
    response = client.post("/api/posts/post-1/rsvp")
    assert response.status_code == 200


def test_rsvp_rejects_neither_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    response = client.post("/api/posts/post-1/rsvp")
    assert response.status_code == 403


def test_rsvp_not_an_event(client, monkeypatch):
    def fail(post_id, uid, going):
        raise communityposts_service.NotAnEventError("This post is not an event")

    monkeypatch.setattr("app.services.communityposts.rsvp", fail)
    response = client.post("/api/posts/post-1/rsvp")
    assert response.status_code == 400


def test_rsvp_post_not_found(client, monkeypatch):
    def fail(post_id, uid, going):
        raise communityposts_service.PostNotFoundError("Post not found")

    monkeypatch.setattr("app.services.communityposts.rsvp", fail)
    response = client.post("/api/posts/nope/rsvp")
    assert response.status_code == 404


def test_cancel_rsvp(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.communityposts.rsvp",
        lambda post_id, uid, going: captured.update(post_id=post_id, uid=uid, going=going)
        or {"attendeeCount": 3, "going": False},
    )
    response = client.delete("/api/posts/post-1/rsvp")
    assert response.status_code == 200
    assert response.json() == {"attendeeCount": 3, "going": False}
    assert captured == {"post_id": "post-1", "uid": TEST_UID, "going": False}


def test_cancel_rsvp_allows_community_accounts(client, monkeypatch):
    result = {"attendeeCount": 3, "going": False}
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: True)
    monkeypatch.setattr("app.services.communityposts.rsvp", lambda post_id, uid, going: result)
    response = client.delete("/api/posts/post-1/rsvp")
    assert response.status_code == 200


def test_cancel_rsvp_rejects_neither_account(client, monkeypatch):
    monkeypatch.setattr("app.services.users.get_profile", lambda uid: None)
    monkeypatch.setattr("app.services.communities.community_exists", lambda uid: False)
    response = client.delete("/api/posts/post-1/rsvp")
    assert response.status_code == 403
