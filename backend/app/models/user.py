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


class OnboardingIn(BaseModel):
    displayName: str
    dob: str  # ISO date, e.g. "1998-04-12"
    gender: str | None = None  # optional profile info; not used to filter the feed
    bio: str = ""
    region: str  # e.g. U-Tsang, Kham, Amdo, or a diaspora city
    languages: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)  # e.g. momo cooking, gorshey, hiking
    socials: Socials  # instagram required; other platforms optional
    location: Location
    preferences: Preferences = Field(default_factory=Preferences)


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
    socials: SocialsUpdate | None = None
    location: Location | None = None  # geohash is recomputed server-side
    preferences: PreferencesUpdate | None = None


class PhotoConfirm(BaseModel):
    storagePath: str
    order: int = 0


class FcmTokenIn(BaseModel):
    token: str
