import os
from typing import Final, Optional

from cryptography.fernet import Fernet, InvalidToken

MIN_KEY_BYTES: Final[int] = 44


class CryptoService:
    """Wrapper around Fernet to keep crypto usage centralized."""

    def __init__(self, key: Optional[str] = None) -> None:
        key_value = key if key is not None else os.getenv("ENCRYPTION_KEY")
        if not key_value:
            raise ValueError("Missing ENCRYPTION_KEY")
        if len(key_value) < MIN_KEY_BYTES:
            raise ValueError("ENCRYPTION_KEY must be a valid Fernet key")
        try:
            self._fernet = Fernet(key_value.encode("utf-8"))
        except (ValueError, TypeError) as error:
            raise ValueError("ENCRYPTION_KEY must be a valid Fernet key") from error

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> str:
        try:
            value = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken as error:
            raise ValueError("Unable to decrypt payload") from error
        return value.decode("utf-8")
