from app.services.messages import MatchClosedError, NotParticipantError
from conftest import TEST_UID


def test_send_message(client, monkeypatch):
    captured = {}

    def fake_send(match_id, uid, text):
        captured.update(match_id=match_id, uid=uid, text=text)
        return "msg-1"

    monkeypatch.setattr("app.services.messages.send_message", fake_send)
    response = client.post("/api/matches/a_b/messages", json={"text": "Tashi delek!"})
    assert response.status_code == 200
    assert response.json() == {"messageId": "msg-1"}
    assert captured == {"match_id": "a_b", "uid": TEST_UID, "text": "Tashi delek!"}


def test_send_message_not_participant(client, monkeypatch):
    def not_mine(match_id, uid, text):
        raise NotParticipantError("Match not found")

    monkeypatch.setattr("app.services.messages.send_message", not_mine)
    assert client.post("/api/matches/a_b/messages", json={"text": "hi"}).status_code == 404


def test_send_message_to_ended_match(client, monkeypatch):
    def closed(match_id, uid, text):
        raise MatchClosedError("This conversation has ended")

    monkeypatch.setattr("app.services.messages.send_message", closed)
    assert client.post("/api/matches/a_b/messages", json={"text": "hi"}).status_code == 400


def test_send_message_empty_text_rejected(client):
    assert client.post("/api/matches/a_b/messages", json={"text": ""}).status_code == 422


def test_send_message_too_long_rejected(client):
    assert client.post("/api/matches/a_b/messages", json={"text": "x" * 2001}).status_code == 422


def test_list_messages(client, monkeypatch):
    messages = [{"messageId": "m2", "senderId": "u2", "text": "hello"}]
    captured = {}

    def fake_list(match_id, uid, limit, before):
        captured.update(match_id=match_id, limit=limit, before=before)
        return messages

    monkeypatch.setattr("app.services.messages.list_messages", fake_list)
    response = client.get("/api/matches/a_b/messages", params={"limit": 10, "before": "m9"})
    assert response.status_code == 200
    assert response.json() == {"messages": messages}
    assert captured == {"match_id": "a_b", "limit": 10, "before": "m9"}


def test_list_messages_not_participant(client, monkeypatch):
    def not_mine(match_id, uid, limit, before):
        raise NotParticipantError("Match not found")

    monkeypatch.setattr("app.services.messages.list_messages", not_mine)
    assert client.get("/api/matches/a_b/messages").status_code == 404


def test_list_messages_limit_capped(client):
    assert client.get("/api/matches/a_b/messages", params={"limit": 101}).status_code == 422


def test_mark_read(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.services.messages.mark_read", lambda match_id, uid: captured.update(m=match_id, u=uid)
    )
    response = client.post("/api/matches/a_b/read")
    assert response.status_code == 200
    assert captured == {"m": "a_b", "u": TEST_UID}


def test_mark_read_not_participant(client, monkeypatch):
    def not_mine(match_id, uid):
        raise NotParticipantError("Match not found")

    monkeypatch.setattr("app.services.messages.mark_read", not_mine)
    assert client.post("/api/matches/a_b/read").status_code == 404


def test_list_sent_messages(client, monkeypatch):
    sent = [{"messageId": "m1", "matchId": "a_b", "senderId": TEST_UID, "text": "hi"}]
    captured = {}

    def fake_sent(uid, limit):
        captured.update(uid=uid, limit=limit)
        return sent

    monkeypatch.setattr("app.services.messages.list_sent", fake_sent)
    response = client.get("/api/messages/sent", params={"limit": 20})
    assert response.status_code == 200
    assert response.json() == {"messages": sent}
    assert captured == {"uid": TEST_UID, "limit": 20}


def test_list_sent_messages_limit_capped(client):
    assert client.get("/api/messages/sent", params={"limit": 201}).status_code == 422
