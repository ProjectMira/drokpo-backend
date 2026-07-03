from functools import lru_cache

import firebase_admin
from firebase_admin import credentials, firestore, storage

from app.config import settings


def _ensure_app() -> None:
    if firebase_admin._apps:
        return
    if settings.google_application_credentials:
        cred = credentials.Certificate(settings.google_application_credentials)
    else:
        # On Cloud Run/Cloud Functions, use the service's attached identity.
        cred = credentials.ApplicationDefault()
    firebase_admin.initialize_app(
        cred,
        {
            "projectId": settings.firebase_project_id,
            "storageBucket": settings.storage_bucket,
        },
    )


@lru_cache
def get_firestore():
    _ensure_app()
    return firestore.client()


@lru_cache
def get_bucket():
    _ensure_app()
    return storage.bucket()
