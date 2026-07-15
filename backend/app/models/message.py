from pydantic import BaseModel, Field, field_validator


class MessageIn(BaseModel):
    # Optional now: a media message (imageUrl/audioUrl) may carry no text at
    # all. validate_shape() (called explicitly by the router, same convention
    # as CommunityPostIn.validate_kind_shape) enforces "text or media".
    text: str | None = Field(default=None, max_length=2000)
    imageUrl: str | None = None
    audioUrl: str | None = None
    audioDurationSec: int | None = None

    @field_validator("text")
    @classmethod
    def text_not_blank_if_present(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("text must not be blank")
        return v

    @field_validator("imageUrl", "audioUrl")
    @classmethod
    def https_only(cls, v: str | None) -> str | None:
        v = v.strip() if v else None
        if v and not v.startswith("https://"):
            raise ValueError("must be an https:// URL")
        return v or None

    def validate_shape(self) -> None:
        has_media = bool(self.imageUrl) or bool(self.audioUrl)
        if not has_media and not self.text:
            raise ValueError("text is required unless imageUrl or audioUrl is set")
        if self.audioUrl and self.audioDurationSec is None:
            raise ValueError("audioDurationSec is required alongside audioUrl")
