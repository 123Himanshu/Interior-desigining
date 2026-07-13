from pydantic import BaseModel, field_validator


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def username_not_empty(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("Username is required")
        return v

    @field_validator("password")
    @classmethod
    def password_not_empty(cls, v):
        if not v or len(v.strip()) < 4:
            raise ValueError("Password must be at least 4 characters")
        return v


class UserCreate(BaseModel):
    username: str
    password: str
    credits: int = 100

    @field_validator("username")
    @classmethod
    def username_valid(cls, v):
        v = (v or "").strip()
        if len(v) < 2 or len(v) > 32:
            raise ValueError("Username must be 2-32 characters")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v):
        if not v or len(v.strip()) < 4:
            raise ValueError("Password must be at least 4 characters")
        return v


class CreditsUpdate(BaseModel):
    username: str
    credits: int


class LibraryAsset(BaseModel):
    id: str
    name: str
    roomTag: str = ""
    objectTag: str = ""
    dataUrl: str = ""


class LibrarySaveRequest(BaseModel):
    assets: list[dict]


class LibraryAddRequest(BaseModel):
    id: str | None = None
    name: str
    roomTag: str = ""
    objectTag: str = ""
    dataUrl: str = ""


class TokenResponse(BaseModel):
    token: str
    username: str
    credits: int


class UserInfo(BaseModel):
    username: str
    credits: int


class EditResponse(BaseModel):
    image_b64: str
    images_b64: list[str]
    format: str
    credits: int


class LibraryResponse(BaseModel):
    enabled: bool
    assets: list[dict]
