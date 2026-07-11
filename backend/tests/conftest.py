import os

# Must be set before app.config is imported — Settings() reads them at import
# time. Tests never touch real Firebase: auth is overridden and every service
# call is monkeypatched.
os.environ.setdefault("FIREBASE_PROJECT_ID", "drokpo-test")
os.environ.setdefault("STORAGE_BUCKET", "drokpo-test.firebasestorage.app")

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_current_uid
from app.main import app

TEST_UID = "test-uid"


@pytest.fixture
def client():
    app.dependency_overrides[get_current_uid] = lambda: TEST_UID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client():
    # No auth override — exercises the real get_current_uid dependency.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def onboarding_payload():
    def make(**overrides):
        payload = {
            "displayName": "Tenzin",
            "dob": "1998-04-12",
            "gender": "male",
            "bio": "New to the city, looking for gorshey partners",
            "region": "Nepal",
            "languages": ["bo", "en"],
            "interests": ["hiking", "momo cooking", "gorshey"],
            "socials": {"instagram": "tenzin_la"},
            "location": {"lat": 27.7172, "lng": 85.324},
        }
        payload.update(overrides)
        return payload

    return make
