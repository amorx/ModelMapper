import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.app import app, process_vault_entry
from src.schemas import VaultAccessRequest
from src.services.crypto import CryptoService
from src.services.vault import SecureVault


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# TEST 1: The Happy Path (Covers encryption and file writing)
def test_vault_stores_encrypted_file(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode("utf-8")
    vault = SecureVault(vault_dir=str(tmp_path), crypto=CryptoService(key=key))
    destination = vault.store_message("secret.txt", "Hello World")
    encrypted = destination.read_text(encoding="utf-8")
    assert destination.name == "secret.txt"
    assert encrypted != "Hello World"

# TEST 2: The Error Path (This gets you from 94% to 100% coverage)
@patch.dict(os.environ, {"ENCRYPTION_KEY": "short"})
def test_vault_raises_error_on_weak_key() -> None:
    with pytest.raises(ValueError, match="valid Fernet key"):
        SecureVault(vault_dir="any_dir")

# TEST 3: Missing Key Path (Ensures security if env var is totally gone)
@patch.dict(os.environ, {}, clear=True)
def test_vault_raises_error_on_missing_key() -> None:
    with pytest.raises(ValueError, match="Missing ENCRYPTION_KEY"):
        SecureVault(vault_dir="any_dir")

# TEST 4: Data Validation (Pydantic integration)
def test_process_vault_entry_success() -> None:
    """Test that valid data passes the bouncer."""
    valid_data = {
        "username": "alex_2026",
        "email": "alex@example.com",
        "access_level": 3
    }
    assert process_vault_entry(valid_data) is True

def test_process_vault_entry_invalid_username() -> None:
    """Test security rejection for malicious characters in username."""
    # Attempting a basic SQL injection/XSS style character input
    invalid_data = {
        "username": "admin; DROP TABLE",
        "email": "alex@example.com",
        "access_level": 1
    }
    assert process_vault_entry(invalid_data) is False

def test_process_vault_entry_invalid_email() -> None:
    """Test that the email-validator integration works."""
    invalid_data = {
        "username": "alex",
        "email": "not-an-email",
        "access_level": 1
    }
    assert process_vault_entry(invalid_data) is False

def test_process_vault_entry_out_of_range_level() -> None:
    """Test that numerical constraints (ge=1, le=5) are enforced."""
    invalid_data = {
        "username": "alex",
        "email": "alex@example.com",
        "access_level": 10  # Out of range
    }
    assert process_vault_entry(invalid_data) is False

def test_schema_direct_validation() -> None:
    """Verify the Pydantic schema raises ValidationError directly."""
    with pytest.raises(ValidationError):
        VaultAccessRequest(
            username="a",  # Too short (min_length=3)
            email="test@test.com",
            access_level=1
        )

def test_username_validator_rejects_non_alnum_characters() -> None:
    """Directly exercise the custom validator rejection branch."""
    with pytest.raises(ValueError, match="Username must be alphanumeric"):
        VaultAccessRequest.username_alphanumeric("bad;name")


@pytest.mark.anyio
async def test_read_root_status_payload() -> None:
    """Ensure FastAPI root handler returns the expected health payload."""
    from src.app import read_root

    payload = await read_root()
    assert payload == {"status": "Secure Vault Online", "version": "1.0.0"}


def test_not_found_handler_includes_security_headers() -> None:
    """Ensure custom 404 handler responds with hardened headers."""
    client = TestClient(app)
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"


def test_robots_and_sitemap_endpoints() -> None:
    """Ensure scanner-noise endpoints exist and return expected content types."""
    client = TestClient(app)

    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "User-agent" in robots.text
    assert robots.headers["content-type"].startswith("text/plain")

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert sitemap.text.startswith("<?xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
