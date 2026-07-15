from app.services import messages as messages_service
from app.services.messages import MatchClosedError, NotParticipantError
from conftest import TEST_UID


def test_send_message(client, monkeypatch):
    captured = {}

    def fake_send(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        captured.update(match_id=match_id, uid=uid, text=text)
        return "msg-1"

    monkeypatch.setattr("app.services.messages.send_message", fake_send)
    response = client.post("/api/matches/a_b/messages", json={"text": "Tashi delek!"})
    assert response.status_code == 200
    assert response.json() == {"messageId": "msg-1"}
    assert captured == {"match_id": "a_b", "uid": TEST_UID, "text": "Tashi delek!"}


def test_send_message_not_participant(client, monkeypatch):
    def not_mine(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        raise NotParticipantError("Match not found")

    monkeypatch.setattr("app.services.messages.send_message", not_mine)
    assert client.post("/api/matches/a_b/messages", json={"text": "hi"}).status_code == 404


def test_send_message_to_ended_match(client, monkeypatch):
    def closed(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        raise MatchClosedError("This conversation has ended")

    monkeypatch.setattr("app.services.messages.send_message", closed)
    assert client.post("/api/matches/a_b/messages", json={"text": "hi"}).status_code == 400


def test_send_message_empty_text_rejected(client):
    assert client.post("/api/matches/a_b/messages", json={"text": ""}).status_code == 422


def test_send_message_too_long_rejected(client):
    assert client.post("/api/matches/a_b/messages", json={"text": "x" * 2001}).status_code == 422


# --- media messages: photo / voice notes -------------------------------------


def test_send_message_requires_text_or_media(client):
    response = client.post("/api/matches/a_b/messages", json={})
    assert response.status_code == 400


def test_send_photo_message(client, monkeypatch):
    captured = {}

    def fake_send(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        captured.update(text=text, image_url=image_url)
        return "msg-2"

    monkeypatch.setattr("app.services.messages.send_message", fake_send)
    response = client.post(
        "/api/matches/a_b/messages",
        json={"imageUrl": "https://cdn.example/chatMedia/u1/photo.jpg"},
    )
    assert response.status_code == 200
    assert captured["text"] is None
    assert captured["image_url"] == "https://cdn.example/chatMedia/u1/photo.jpg"


def test_send_voice_message(client, monkeypatch):
    captured = {}

    def fake_send(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        captured.update(audio_url=audio_url, audio_duration_sec=audio_duration_sec)
        return "msg-3"

    monkeypatch.setattr("app.services.messages.send_message", fake_send)
    response = client.post(
        "/api/matches/a_b/messages",
        json={"audioUrl": "https://cdn.example/chatMedia/u1/voice.m4a", "audioDurationSec": 12},
    )
    assert response.status_code == 200
    assert captured["audio_url"] == "https://cdn.example/chatMedia/u1/voice.m4a"
    assert captured["audio_duration_sec"] == 12


def test_send_voice_message_requires_duration(client):
    response = client.post(
        "/api/matches/a_b/messages",
        json={"audioUrl": "https://cdn.example/chatMedia/u1/voice.m4a"},
    )
    assert response.status_code == 400


def test_send_message_media_urls_must_be_https(client):
    response = client.post(
        "/api/matches/a_b/messages",
        json={"imageUrl": "http://insecure.example/photo.jpg"},
    )
    assert response.status_code == 422


def test_send_message_with_text_and_photo(client, monkeypatch):
    captured = {}

    def fake_send(match_id, uid, text, image_url=None, audio_url=None, audio_duration_sec=None):
        captured.update(text=text, image_url=image_url)
        return "msg-4"

    monkeypatch.setattr("app.services.messages.send_message", fake_send)
    response = client.post(
        "/api/matches/a_b/messages",
        json={"text": "look at this", "imageUrl": "https://cdn.example/chatMedia/u1/photo.jpg"},
    )
    assert response.status_code == 200
    assert captured["text"] == "look at this"
    assert captured["image_url"] == "https://cdn.example/chatMedia/u1/photo.jpg"


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


# --- send_message() service — media fields actually persisted --------------


class _StubSnap:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return self._data


class _StubRef:
    def __init__(self, id_):
        self.id = id_


class _StubMessagesCollection:
    def __init__(self):
        self.added = None

    def add(self, doc):
        self.added = doc
        return (None, _StubRef("msg-1"))


class _StubMatchRef:
    def __init__(self, match_data, messages_collection):
        self._data = match_data
        self._messages = messages_collection

    def get(self):
        return _StubSnap(self._data)

    def collection(self, name):
        return self._messages


class _StubDB:
    def __init__(self, match_ref):
        self._match_ref = match_ref

    def collection(self, name):
        return self

    def document(self, match_id):
        return self._match_ref


def test_send_message_service_persists_media_fields(monkeypatch):
    messages_collection = _StubMessagesCollection()
    match_ref = _StubMatchRef({"users": [TEST_UID, "other"], "status": "active"}, messages_collection)
    monkeypatch.setattr(messages_service, "get_firestore", lambda: _StubDB(match_ref))

    messages_service.send_message(
        "a_b", TEST_UID, None, image_url="https://cdn.example/photo.jpg", audio_url=None, audio_duration_sec=None
    )

    assert messages_collection.added["text"] is None
    assert messages_collection.added["imageUrl"] == "https://cdn.example/photo.jpg"
    assert messages_collection.added["audioUrl"] is None
    assert messages_collection.added["senderId"] == TEST_UID


def test_send_message_service_persists_voice_fields(monkeypatch):
    messages_collection = _StubMessagesCollection()
    match_ref = _StubMatchRef({"users": [TEST_UID, "other"], "status": "active"}, messages_collection)
    monkeypatch.setattr(messages_service, "get_firestore", lambda: _StubDB(match_ref))

    messages_service.send_message(
        "a_b", TEST_UID, None, audio_url="https://cdn.example/voice.m4a", audio_duration_sec=8
    )

    assert messages_collection.added["audioUrl"] == "https://cdn.example/voice.m4a"
    assert messages_collection.added["audioDurationSec"] == 8
