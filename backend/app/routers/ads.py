from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_current_uid
from app.services import ads as ads_service

router = APIRouter(prefix="/ads", tags=["ads"])


class AdEventIn(BaseModel):
    event: Literal["impression", "click"]


@router.post("/{ad_id}/events")
def record_ad_event(ad_id: str, payload: AdEventIn, uid: str = Depends(get_current_uid)):
    # Fire-and-forget analytics from the client: an ad card was shown, or its
    # link was opened.
    try:
        ads_service.record_event(ad_id, payload.event)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
