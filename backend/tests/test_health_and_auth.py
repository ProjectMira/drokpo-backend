def test_health(anon_client):
    assert anon_client.get("/health").json() == {"status": "ok"}


def test_api_health(anon_client):
    assert anon_client.get("/api/health").json() == {"status": "ok"}


def test_missing_auth_header_rejected(anon_client):
    response = anon_client.get("/api/profile/me")
    assert response.status_code == 422  # required Authorization header absent


def test_non_bearer_token_rejected(anon_client):
    response = anon_client.get("/api/profile/me", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401
