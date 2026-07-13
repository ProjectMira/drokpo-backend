from fastapi import Depends, Header, HTTPException, status
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


# --- Role-based access control ------------------------------------------
#
# An account's role is never stored — it's fully determined by which doc
# exists (users/{uid} => person, communities/{uid} => community), and
# onboarding guarantees at most one of those exists for a given uid. These
# dependencies resolve that once per request instead of leaving each router
# to remember its own ad-hoc check.


async def require_person_uid(uid: str = Depends(get_current_uid)) -> str:
    from app.services import users as users_service

    if not users_service.get_profile(uid):
        raise HTTPException(status_code=403, detail="This action requires a personal account")
    return uid


async def require_community_uid(uid: str = Depends(get_current_uid)) -> str:
    from app.services import communities as communities_service

    if not communities_service.community_exists(uid):
        raise HTTPException(status_code=403, detail="This action requires a community account")
    return uid
