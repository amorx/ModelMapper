from pydantic import BaseModel, EmailStr, Field, field_validator

class VaultAccessRequest(BaseModel):
    # Enforce string length to prevent Buffer Overflow style attacks
    username: str = Field(..., min_length=3, max_length=20, pattern="^[a-zA-Z0-9_]+$")

    # EmailStr validates the format automatically
    email: EmailStr

    # Enforce a strict range for numerical data
    access_level: int = Field(..., ge=1, le=5)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, value: str) -> str:
        # Permit underscores to match the declared regex pattern.
        if not value.replace("_", "").isalnum():
            raise ValueError("Username must be alphanumeric with optional underscores")
        return value
