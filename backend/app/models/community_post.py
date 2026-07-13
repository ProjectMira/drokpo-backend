from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, field_validator

MIN_POLL_OPTIONS = 2
MAX_POLL_OPTIONS = 4
MAX_LOCATION_LEN = 200


def _parse_event_at(v: str) -> datetime:
    """ISO 8601 with an explicit offset (the client sends the device's local
    offset, same convention this codebase already uses for `createdAt`-style
    strings on the wire) — reject naive datetimes so "when" is unambiguous."""
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError as exc:
        raise ValueError("eventAt must be an ISO 8601 datetime, e.g. 2026-08-15T18:00:00+05:30") from exc
    if parsed.tzinfo is None:
        raise ValueError("eventAt must include a UTC offset")
    return parsed


class CommunityPostIn(BaseModel):
    kind: Literal["announcement", "link", "poll", "event"]
    title: str
    body: str = ""
    imageUrl: str | None = None
    # Alternative to imageUrl: a path the community already uploaded under its
    # own communities/{cid}/photos/ prefix, resolved to a download URL at
    # creation time — same dual shape as ads (docs/ADS.md).
    photoStoragePath: str | None = None
    linkUrl: str | None = None
    ctaLabel: str | None = None
    pollOptions: list[str] | None = None
    eventAt: str | None = None
    location: str | None = None

    @field_validator("title")
    @classmethod
    def title_len(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 160:
            raise ValueError("title is required and must be at most 160 characters")
        return v

    @field_validator("body")
    @classmethod
    def body_len(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError("body must be at most 2000 characters")
        return v

    @field_validator("imageUrl")
    @classmethod
    def image_https(cls, v: str | None) -> str | None:
        v = v.strip() if v else None
        if v and not v.startswith("https://"):
            raise ValueError("imageUrl must be an https:// URL")
        return v or None

    @field_validator("linkUrl")
    @classmethod
    def link_https(cls, v: str | None) -> str | None:
        v = v.strip() if v else None
        if v and not v.startswith("https://"):
            raise ValueError("linkUrl must be an https:// URL")
        return v or None

    @field_validator("location")
    @classmethod
    def location_len(cls, v: str | None) -> str | None:
        v = v.strip() if v else None
        if v and len(v) > MAX_LOCATION_LEN:
            raise ValueError(f"location must be at most {MAX_LOCATION_LEN} characters")
        return v or None

    def validate_kind_shape(self) -> None:
        """Cross-field rules a single-field validator can't express."""
        if self.kind == "link" and not self.linkUrl:
            raise ValueError("linkUrl is required for a link post")
        if self.kind == "poll":
            options = [o.strip() for o in (self.pollOptions or []) if o.strip()]
            if not (MIN_POLL_OPTIONS <= len(options) <= MAX_POLL_OPTIONS):
                raise ValueError(
                    f"poll posts need {MIN_POLL_OPTIONS}-{MAX_POLL_OPTIONS} non-empty options"
                )
            if len(set(options)) != len(options):
                raise ValueError("poll options must be unique")
        if self.kind == "event":
            if not self.eventAt:
                raise ValueError("eventAt is required for an event post")
            event_at = _parse_event_at(self.eventAt)
            if event_at <= datetime.now(timezone.utc):
                raise ValueError("eventAt must be in the future")


class CommunityPostUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    imageUrl: str | None = None
    linkUrl: str | None = None
    ctaLabel: str | None = None
    active: bool | None = None

    @field_validator("title")
    @classmethod
    def title_len(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v or len(v) > 160:
            raise ValueError("title must be 1-160 characters")
        return v

    @field_validator("body")
    @classmethod
    def body_len(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 2000:
            raise ValueError("body must be at most 2000 characters")
        return v

    @field_validator("imageUrl", "linkUrl")
    @classmethod
    def https_only(cls, v: str | None) -> str | None:
        if v and not v.startswith("https://"):
            raise ValueError("must be an https:// URL")
        return v


class VoteIn(BaseModel):
    optionId: str
