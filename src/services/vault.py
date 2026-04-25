from pathlib import Path
from typing import Optional

from src.services.crypto import CryptoService


class SecureVault:
    def __init__(self, vault_dir: str, crypto: Optional[CryptoService] = None) -> None:
        self._vault_dir = Path(vault_dir)
        self._crypto = crypto or CryptoService()

    def store_message(self, filename: str, message: str) -> Path:
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        destination = self._vault_dir / filename
        destination.write_text(self._crypto.encrypt(message), encoding="utf-8")
        return destination
