from pydantic import BaseModel, Field


class Location(BaseModel):
    lat: float
    lng: float


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
    gender: str
    seekingGenders: list[str]
    bio: str = ""
    region: str  # e.g. U-Tsang, Kham, Amdo, or a diaspora city
    languages: list[str] = Field(default_factory=list)
    location: Location
    preferences: Preferences = Field(default_factory=Preferences)


class ProfileUpdate(BaseModel):
    displayName: str | None = None
    bio: str | None = None
    occupation: str | None = None
    education: str | None = None
    region: str | None = None
    languages: list[str] | None = None
    seekingGenders: list[str] | None = None
    preferences: PreferencesUpdate | None = None


class PhotoConfirm(BaseModel):
    storagePath: str
    order: int = 0


class FcmTokenIn(BaseModel):
    token: str
