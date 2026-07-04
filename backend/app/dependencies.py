from fastapi import Header, HTTPException, status
from firebase_admin import auth as firebase_auth

from app.firebase import ensure_app


async def get_current_uid(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        # verify_id_token needs the default Firebase app, which is otherwise
        # only created lazily by the firestore/storage helpers.
        ensure_app()
        decoded = firebase_auth.verify_id_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    return decoded["uid"]
