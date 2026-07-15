from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import require_account_uid
from app.models.message import MessageIn
from app.services import messages as messages_service
from app.services.messages import MatchClosedError, NotParticipantError

router = APIRouter(tags=["messages"])


@router.post("/matches/{match_id}/messages")
def send_message(match_id: str, payload: MessageIn, uid: str = Depends(require_account_uid)):
    try:
        payload.validate_shape()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        message_id = messages_service.send_message(
            match_id,
            uid,
            payload.text,
            image_url=payload.imageUrl,
            audio_url=payload.audioUrl,
            audio_duration_sec=payload.audioDurationSec,
        )
    except NotParticipantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MatchClosedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"messageId": message_id}


@router.get("/matches/{match_id}/messages")
def list_messages(
    match_id: str,
    uid: str = Depends(require_account_uid),
    limit: int = Query(default=30, le=100),
    before: str | None = Query(default=None, description="Message ID to page back from"),
):
    try:
        messages = messages_service.list_messages(match_id, uid, limit, before)
    except NotParticipantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"messages": messages}


@router.post("/matches/{match_id}/read")
def mark_read(match_id: str, uid: str = Depends(require_account_uid)):
    try:
        messages_service.mark_read(match_id, uid)
    except NotParticipantError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/messages/sent")
def list_sent_messages(uid: str = Depends(require_account_uid), limit: int = Query(default=50, le=200)):
    return {"messages": messages_service.list_sent(uid, limit)}
