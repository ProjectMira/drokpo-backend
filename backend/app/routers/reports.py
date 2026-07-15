from fastapi import APIRouter, Depends

from app.dependencies import require_account_uid
from app.models.report import ReportIn
from app.services import reports as reports_service

router = APIRouter(tags=["safety"])


@router.post("/reports")
def report_user(payload: ReportIn, uid: str = Depends(require_account_uid)):
    reports_service.create_report(uid, payload)
    return {"ok": True}


@router.post("/blocks/{target_uid}")
def block_user(target_uid: str, uid: str = Depends(require_account_uid)):
    reports_service.block_user(uid, target_uid)
    return {"ok": True}


@router.delete("/blocks/{target_uid}")
def unblock_user(target_uid: str, uid: str = Depends(require_account_uid)):
    reports_service.unblock_user(uid, target_uid)
    return {"ok": True}
