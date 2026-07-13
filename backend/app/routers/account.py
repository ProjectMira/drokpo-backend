from fastapi import APIRouter, Depends

from app.dependencies import get_current_uid
from app.services import communities as communities_service
from app.services import users as users_service

router = APIRouter(prefix="/account", tags=["account"])


@router.get("")
def get_account(uid: str = Depends(get_current_uid)):
    """Single call the app makes at launch to decide which experience (and
    which onboarding, if any) to route into — person, community, or neither
    yet. Checking both collections here means the client never has to guess
    which profile endpoint to call first.
    """
    profile = users_service.get_profile(uid)
    if profile:
        return {"accountType": "person", "profile": profile, "community": None}
    community = communities_service.get_community(uid)
    if community:
        return {"accountType": "community", "profile": None, "community": community}
    return {"accountType": "none", "profile": None, "community": None}
