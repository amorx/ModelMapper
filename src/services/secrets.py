import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Protocol

from src.services.crypto import EncryptionService

FINGERPRINT_SALT = b"modelmapper-provider-secret-v1"


class SecretStore(Protocol):
    def get_secret_for_provider(self, provider_id: int) -> str | None:
        ...

    def store_secret_for_provider(self, provider_id: int, encrypted_secret: str, fingerprint: str) -> None:
        ...


@dataclass(frozen=True)
class SecretsResolver:
    store: SecretStore | None = None
    encryption: EncryptionService | None = None

    def resolve(self, provider: dict[str, object]) -> str | None:
        source = str(provider.get("api_key_source") or "none")
        if source == "none":
            return None
        if source == "env":
            env_var = provider.get("api_key_env_var")
            if not isinstance(env_var, str) or not env_var:
                raise ValueError("Provider is missing an API key environment variable")
            value = os.getenv(env_var)
            if not value:
                raise ValueError("Configured API key environment variable is not set")
            return value
        if source == "encrypted":
            if self.store is None or self.encryption is None:
                raise ValueError("Encrypted secret storage is not configured")
            encrypted = self.store.get_secret_for_provider(int(provider["id"]))
            if encrypted is None:
                raise ValueError("Encrypted provider secret is missing")
            return self.encryption.decrypt(encrypted)
        raise ValueError("Unsupported API key source")

    def store_encrypted(self, provider_id: int, api_key: str) -> str:
        if self.store is None or self.encryption is None:
            raise ValueError("Encrypted secret storage is not configured")
        encrypted = self.encryption.encrypt(api_key)
        fingerprint = fingerprint_secret(api_key)
        self.store.store_secret_for_provider(provider_id, encrypted, fingerprint)
        return fingerprint


def fingerprint_secret(secret: str) -> str:
    digest = hmac.new(FINGERPRINT_SALT, secret.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:16]
