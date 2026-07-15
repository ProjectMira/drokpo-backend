from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.dependencies import get_current_uid, require_account_uid
from app.models.community_post import CommentIn, CommentVoteIn, VoteIn
from app.services import comments as comments_service
from app.services import communityposts as communityposts_service

router = APIRouter(prefix="/posts", tags=["community-posts"])


class PostEventIn(BaseModel):
    event: Literal["impression", "click"]


@router.post("/{post_id}/vote")
def vote_on_post(post_id: str, payload: VoteIn, uid: str = Depends(require_account_uid)):
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
def like_post(post_id: str, uid: str = Depends(require_account_uid)):
    try:
        return communityposts_service.like(uid, post_id)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{post_id}/like")
def unlike_post(post_id: str, uid: str = Depends(require_account_uid)):
    communityposts_service.unlike(uid, post_id)
    return {"ok": True}


@router.post("/{post_id}/rsvp")
def rsvp_to_event(post_id: str, uid: str = Depends(require_account_uid)):
    try:
        return communityposts_service.rsvp(post_id, uid, going=True)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except communityposts_service.NotAnEventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{post_id}/rsvp")
def cancel_rsvp(post_id: str, uid: str = Depends(require_account_uid)):
    try:
        return communityposts_service.rsvp(post_id, uid, going=False)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except communityposts_service.NotAnEventError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- comments ----------------------------------------------------------------


@router.post("/{post_id}/comments")
def create_comment(post_id: str, payload: CommentIn, uid: str = Depends(require_account_uid)):
    try:
        return comments_service.create_comment(post_id, uid, payload)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except comments_service.CommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{post_id}/comments")
def list_comments(
    post_id: str,
    uid: str = Depends(require_account_uid),
    limit: int = Query(default=comments_service.DEFAULT_LIMIT, le=50),
    before: str | None = Query(default=None, description="Comment ID to page back from"),
):
    return {"comments": comments_service.list_comments(post_id, uid, limit, before)}


@router.get("/{post_id}/comments/{comment_id}/replies")
def list_replies(
    post_id: str,
    comment_id: str,
    uid: str = Depends(require_account_uid),
    limit: int = Query(default=comments_service.DEFAULT_REPLY_LIMIT, le=50),
    before: str | None = Query(default=None, description="Reply ID to page back from"),
):
    return {"replies": comments_service.list_replies(post_id, comment_id, uid, limit, before)}


@router.delete("/{post_id}/comments/{comment_id}")
def delete_comment(post_id: str, comment_id: str, uid: str = Depends(require_account_uid)):
    try:
        comments_service.delete_comment(post_id, comment_id, uid)
    except communityposts_service.PostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except comments_service.CommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except comments_service.NotAllowedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True}


@router.put("/{post_id}/comments/{comment_id}/vote")
def vote_on_comment(post_id: str, comment_id: str, payload: CommentVoteIn, uid: str = Depends(require_account_uid)):
    try:
        return comments_service.vote_comment(post_id, comment_id, uid, payload.value)
    except comments_service.CommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{post_id}/comments/{comment_id}/vote")
def clear_comment_vote(post_id: str, comment_id: str, uid: str = Depends(require_account_uid)):
    try:
        return comments_service.vote_comment(post_id, comment_id, uid, None)
    except comments_service.CommentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
