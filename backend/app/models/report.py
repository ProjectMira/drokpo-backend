from pydantic import BaseModel


class ReportIn(BaseModel):
    reportedUid: str
    reason: str
    note: str = ""
