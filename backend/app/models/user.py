from pydantic import BaseModel, Field, field_validator


class Location(BaseModel):
    lat: float
    lng: float


class Socials(BaseModel):
    instagram: str  # required — the one social handle every profile must have
    youtube: str | None = None
    tiktok: str | None = None
    facebook: str | None = None
    x: str | None = None
    wechat: str | None = None

    @field_validator("instagram")
    @classmethod
    def instagram_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("instagram handle is required")
        return v


class SocialsUpdate(BaseModel):
    instagram: str | None = None
    youtube: str | None = None
    tiktok: str | None = None
    facebook: str | None = None
    x: str | None = None
    wechat: str | None = None

    @field_validator("instagram")
    @classmethod
    def instagram_not_blank(cls, v: str | None) -> str | None:
        # instagram can be changed but never cleared — it's the required social.
        if v is not None and not v.strip():
            raise ValueError("instagram handle cannot be empty")
        return v.strip() if v is not None else None


class Preferences(BaseModel):
    ageMin: int = 18
    ageMax: int = 99
    distanceKm: int = 50


class PreferencesUpdate(BaseModel):
    ageMin: int | None = None
    ageMax: int | None = None
    distanceKm: int | None = None


# Free-form profile Q&A ("Chai or butter tea?", "Places I've travelled to", …).
# Keys are stable question ids the client defines; values are the answers.
MAX_ANSWERS = 30
MAX_ANSWER_KEY_LEN = 40
MAX_ANSWER_LEN = 500


def clean_answers(answers: dict[str, str] | None) -> dict[str, str] | None:
    if answers is None:
        return None
    if len(answers) > MAX_ANSWERS:
        raise ValueError(f"At most {MAX_ANSWERS} answers allowed")
    cleaned: dict[str, str] = {}
    for key, value in answers.items():
        key, value = key.strip(), value.strip()
        if not key or len(key) > MAX_ANSWER_KEY_LEN:
            raise ValueError("Answer keys must be 1–40 characters")
        if len(value) > MAX_ANSWER_LEN:
            raise ValueError(f"Answers must be at most {MAX_ANSWER_LEN} characters")
        if value:  # an emptied answer is simply dropped
            cleaned[key] = value
    return cleaned


class OnboardingIn(BaseModel):
    displayName: str
    dob: str  # ISO date, e.g. "1998-04-12"
    gender: str | None = None  # optional profile info; not used to filter the feed
    bio: str = ""
    occupation: str = ""
    education: str = ""  # education level, e.g. "Bachelor's"
    region: str  # e.g. U-Tsang, Kham, Amdo, or a diaspora city
    languages: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)  # e.g. momo cooking, gorshey, hiking
    answers: dict[str, str] = Field(default_factory=dict)  # profile Q&A prompts
    socials: Socials  # instagram required; other platforms optional
    location: Location
    preferences: Preferences = Field(default_factory=Preferences)

    @field_validator("answers")
    @classmethod
    def _clean_answers(cls, v: dict[str, str]) -> dict[str, str]:
        return clean_answers(v) or {}


class ProfileUpdate(BaseModel):
    displayName: str | None = None
    bio: str | None = None
    dob: str | None = None  # ISO date
    gender: str | None = None
    occupation: str | None = None
    education: str | None = None
    region: str | None = None
    languages: list[str] | None = None
    interests: list[str] | None = None
    answers: dict[str, str] | None = None  # replaced wholesale when present
    socials: SocialsUpdate | None = None
    location: Location | None = None  # geohash is recomputed server-side
    preferences: PreferencesUpdate | None = None

    @field_validator("answers")
    @classmethod
    def _clean_answers(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return clean_answers(v)


class PhotoConfirm(BaseModel):
    storagePath: str
    order: int = 0


class PhotoOrderIn(BaseModel):
    storagePaths: list[str]


class FcmTokenIn(BaseModel):
    token: str
