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
