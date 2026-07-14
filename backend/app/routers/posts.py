from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_current_uid, require_person_uid
from app.models.community_post import VoteIn
from app.services import communityposts as communityposts_service

router = APIRouter(prefix="/posts", tags=["community-posts"])


class PostEventIn(BaseModel):
    event: Literal["impression", "click"]


@router.post("/{post_id}/vote")
def vote_on_post(post_id: str, payload: VoteIn, uid: str = Depends(require_person_uid)):
    try:
        return communityposts_service.vote(post_id, uid, payload.optionId)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except communityposts_service.NotAPollError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except communityposts_service.InvalidOptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{post_id}/events")
def record_post_event(post_id: str, payload: PostEventIn, uid: str = Depends(get_current_uid)):
    try:
        communityposts_service.record_event(post_id, payload.event)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.put("/{post_id}/like")
def like_post(post_id: str, uid: str = Depends(require_person_uid)):
    try:
        return communityposts_service.like(uid, post_id)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{post_id}/like")
def unlike_post(post_id: str, uid: str = Depends(require_person_uid)):
    communityposts_service.unlike(uid, post_id)
    return {"ok": True}


@router.post("/{post_id}/rsvp")
def rsvp_to_event(post_id: str, uid: str = Depends(require_person_uid)):
    try:
        return communityposts_service.rsvp(post_id, uid, going=True)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except communityposts_service.NotAnEventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{post_id}/rsvp")
def cancel_rsvp(post_id: str, uid: str = Depends(require_person_uid)):
    try:
        return communityposts_service.rsvp(post_id, uid, going=False)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except communityposts_service.NotAnEventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
