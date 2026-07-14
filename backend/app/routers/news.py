from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_current_uid, require_person_uid
from app.services import news as news_service

router = APIRouter(prefix="/news", tags=["news"])


class NewsEventIn(BaseModel):
    event: Literal["impression", "click"]


@router.post("/{news_id}/events")
def record_news_event(news_id: str, payload: NewsEventIn, uid: str = Depends(get_current_uid)):
    try:
        news_service.record_event(news_id, payload.event)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.put("/{news_id}/like")
def like_news(news_id: str, uid: str = Depends(require_person_uid)):
    try:
        return news_service.like(uid, news_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{news_id}/like")
def unlike_news(news_id: str, uid: str = Depends(require_person_uid)):
    news_service.unlike(uid, news_id)
    return {"ok": True}
