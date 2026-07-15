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


MAX_COMMENT_TEXT_LEN = 2200
MAX_COMMENT_AUDIO_SEC = 60


class CommentIn(BaseModel):
    text: str | None = None
    # Alternative to text: a path the caller already uploaded under their own
    # commentAudio/{uid}/ Storage prefix, resolved to a download URL by the
    # service — same dual-input shape as a post's photoStoragePath.
    audioStoragePath: str | None = None
    audioDurationSec: int | None = None
    # None = a top-level comment; otherwise the comment being replied to
    # (replying to a reply is coerced server-side to the same top-level thread).
    parentId: str | None = None

    @field_validator("text")
    @classmethod
    def text_len(cls, v: str | None) -> str | None:
        v = v.strip() if v else None
        if v and len(v) > MAX_COMMENT_TEXT_LEN:
            raise ValueError(f"text must be at most {MAX_COMMENT_TEXT_LEN} characters")
        return v or None

    @field_validator("audioDurationSec")
    @classmethod
    def duration_bounds(cls, v: int | None) -> int | None:
        if v is not None and not (0 < v <= MAX_COMMENT_AUDIO_SEC):
            raise ValueError(f"audioDurationSec must be between 1 and {MAX_COMMENT_AUDIO_SEC}")
        return v

    def validate_shape(self) -> None:
        has_text = bool(self.text)
        has_audio = bool(self.audioStoragePath)
        if has_text == has_audio:
            raise ValueError("exactly one of text or audioStoragePath is required")
        if has_audio and self.audioDurationSec is None:
            raise ValueError("audioDurationSec is required alongside audioStoragePath")


class CommentVoteIn(BaseModel):
    value: Literal["like", "dislike"]
