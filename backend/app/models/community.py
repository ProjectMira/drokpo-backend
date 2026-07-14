from pydantic import BaseModel, field_validator

from app.models.user import SocialsUpdate


class ContactPerson(BaseModel):
    name: str
    role: str | None = None
    phone: str | None = None
    email: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("contact person name is required")
        return v


class ContactPersonUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    phone: str | None = None
    email: str | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("contact person name cannot be empty")
        return v.strip() if v is not None else None


class Address(BaseModel):
    line1: str | None = None
    city: str
    state: str | None = None
    country: str
    postalCode: str | None = None

    @field_validator("city", "country")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("field is required")
        return v


class AddressUpdate(BaseModel):
    line1: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postalCode: str | None = None


def _https_or_none(v: str | None) -> str | None:
    v = v.strip() if v else None
    if v and not v.startswith("https://"):
        raise ValueError("must be an https:// URL")
    return v or None


def _valid_email(v: str) -> str:
    v = v.strip()
    if not v or "@" not in v:
        raise ValueError("a valid email is required")
    return v


class CommunityOnboardingIn(BaseModel):
    name: str
    description: str
    website: str | None = None
    phone: str | None = None
    # Required (unlike a person's onboarding, where it's optional profile
    # info) — the approval-notification email (docs/COMMUNITIES.md) needs
    # somewhere to go, and never clearable once set, same as a person's
    # required Instagram handle.
    email: str
    contactPerson: ContactPerson
    address: Address
    # Unlike a person's onboarding, no social handle is required here — a
    # community may have none at all, so this uses the all-optional shape
    # rather than the person Socials model (which requires instagram).
    socials: SocialsUpdate | None = None

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str) -> str:
        v = v.strip()
        if not (2 <= len(v) <= 80):
            raise ValueError("name must be 2-80 characters")
        return v

    @field_validator("description")
    @classmethod
    def description_len(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 2000:
            raise ValueError("description is required and must be at most 2000 characters")
        return v

    @field_validator("website")
    @classmethod
    def website_https(cls, v: str | None) -> str | None:
        return _https_or_none(v)

    @field_validator("email")
    @classmethod
    def email_required(cls, v: str) -> str:
        return _valid_email(v)


class CommunityUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    website: str | None = None
    phone: str | None = None
    email: str | None = None
    contactPerson: ContactPersonUpdate | None = None
    address: AddressUpdate | None = None
    socials: SocialsUpdate | None = None

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not (2 <= len(v) <= 80):
            raise ValueError("name must be 2-80 characters")
        return v

    @field_validator("description")
    @classmethod
    def description_len(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v or len(v) > 2000:
            raise ValueError("description must be 1-2000 characters")
        return v

    @field_validator("website")
    @classmethod
    def website_https(cls, v: str | None) -> str | None:
        # Unlike onboarding, an explicit "" survives here — it means "clear
        # this field" (update_community maps it to a field delete).
        if v is None:
            return None
        v = v.strip()
        if v and not v.startswith("https://"):
            raise ValueError("must be an https:// URL")
        return v

    @field_validator("email")
    @classmethod
    def email_not_blank(cls, v: str | None) -> str | None:
        # Can be changed but never cleared — same convention as a person's
        # Instagram handle (SocialsUpdate.instagram_not_blank).
        if v is None:
            return None
        return _valid_email(v)


class CommunityPhotoConfirm(BaseModel):
    storagePath: str
    order: int = 0


class CommunityPhotoOrderIn(BaseModel):
    storagePaths: list[str]
