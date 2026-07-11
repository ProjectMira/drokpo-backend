from conftest import TEST_UID


def test_report_user(client, monkeypatch):
    captured = {}

    def fake_report(uid, payload):
        captured.update(uid=uid, payload=payload)

    monkeypatch.setattr("app.services.reports.create_report", fake_report)
    response = client.post("/api/reports", json={"reportedUid": "bad-uid", "reason": "spam"})
    assert response.status_code == 200
    assert captured["uid"] == TEST_UID
    assert captured["payload"].reportedUid == "bad-uid"
    assert captured["payload"].note == ""


def test_report_requires_reason(client):
    assert client.post("/api/reports", json={"reportedUid": "bad-uid"}).status_code == 422


def test_block_user(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.reports.block_user", lambda uid, target: captured.update(u=uid, t=target)
    )
    response = client.post("/api/blocks/bad-uid")
    assert response.status_code == 200
    assert captured == {"u": TEST_UID, "t": "bad-uid"}


def test_unblock_user(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.reports.unblock_user", lambda uid, target: captured.update(u=uid, t=target)
    )
    response = client.delete("/api/blocks/bad-uid")
    assert response.status_code == 200
    assert captured == {"u": TEST_UID, "t": "bad-uid"}


def test_block_user_unmatches_active_match(monkeypatch):
    """Blocking flips an active deterministic match to 'unmatched'."""
    from app.services import reports as reports_service
    from app.services.matching import _match_id

    committed = {"batch": False}
    match_updates = {}

    class StubBatch:
        def set(self, ref, data):
            pass

        def commit(self):
            committed["batch"] = True

    class StubMatchSnap:
        exists = True

        def to_dict(self):
            return {"status": "active"}

    class StubMatchRef:
        def get(self):
            return StubMatchSnap()

        def update(self, data):
            match_updates.update(data)

    class StubCollection:
        def document(self, doc_id):
            # blocks/... refs are only used by the batch (no-op); the match ref
            # is the one whose status we assert on.
            if doc_id == _match_id("u1", "u2"):
                return StubMatchRef()
            return object()

    class StubDB:
        def collection(self, name):
            return StubCollection()

        def batch(self):
            return StubBatch()

    monkeypatch.setattr(reports_service, "get_firestore", lambda: StubDB())
    # _block_refs calls .collection(...).document(...).collection(...).document(...);
    # give it a chainable no-op.
    monkeypatch.setattr(reports_service, "_block_refs", lambda db, uid, target: (object(), object()))

    reports_service.block_user("u1", "u2")

    assert committed["batch"]
    assert match_updates == {"status": "unmatched"}


def test_block_user_ignores_inactive_match(monkeypatch):
    """Blocking doesn't touch an already-unmatched (or missing) match."""
    from app.services import reports as reports_service

    match_updates = {}

    class StubMatchSnap:
        exists = True

        def to_dict(self):
            return {"status": "unmatched"}

    class StubMatchRef:
        def get(self):
            return StubMatchSnap()

        def update(self, data):
            match_updates.update(data)

    class StubBatch:
        def set(self, ref, data):
            pass

        def commit(self):
            pass

    class StubDB:
        def collection(self, name):
            class C:
                def document(self, doc_id):
                    return StubMatchRef()

            return C()

        def batch(self):
            return StubBatch()

    monkeypatch.setattr(reports_service, "get_firestore", lambda: StubDB())
    monkeypatch.setattr(reports_service, "_block_refs", lambda db, uid, target: (object(), object()))

    reports_service.block_user("u1", "u2")

    assert match_updates == {}
