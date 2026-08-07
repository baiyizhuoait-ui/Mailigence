"""Credential encryption (Fernet, symmetric authenticated encryption).

All mailbox secrets — app-specific passwords and OAuth2 refresh tokens — are
encrypted here before ever touching the database, and decrypted only in memory
for the lifetime of an IMAP connection. Fernet provides confidentiality + 
integrity (HMAC) and the key never leaves the server process.

Operational notes
-----------------
* The master key lives in ``CREDENTIAL_ENCRYPTION_KEY`` (env). Generate with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
* Rotating the key requires decrypting with the old key and re-encrypting; a
  migration helper should be added before production rotation.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class EncryptionError(RuntimeError):
    """Raised when the encryption key is missing or a token cannot be decrypted."""


def _fernet() -> Fernet:
    if not settings.credential_encryption_key:
        raise EncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Generate one (see .env.example) "
            "and restart the server before creating mailbox accounts."
        )
    try:
        return Fernet(settings.credential_encryption_key.encode())
    except (ValueError, TypeError) as exc:  # malformed key
        raise EncryptionError(f"Invalid CREDENTIAL_ENCRYPTION_KEY: {exc}") from exc


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext secret, returning a Fernet token as a string."""
    if plaintext is None:
        raise EncryptionError("Cannot encrypt empty credential.")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt a stored Fernet token back to plaintext (in-memory only)."""
    if not token:
        raise EncryptionError("Empty credential token.")
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError(
            "Credential could not be decrypted — key mismatch or data corruption."
        ) from exc
