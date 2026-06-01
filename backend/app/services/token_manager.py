"""
app/services/token_manager.py
==============================
AES-256-GCM token encryption/decryption for Zerodha credentials.

WHY ENCRYPTION:
----------------
Zerodha access tokens are short-lived (expire 6 AM IST) but still
represent real-money broker access during their validity window.
Storing them in plaintext (either broker_token.json or Redis raw)
means any read access to storage = full broker access.

AES-256-GCM provides:
  - Confidentiality (unreadable without ENCRYPTION_KEY)
  - Integrity (GCM authentication tag detects tampering)

SETUP:
------
Generate a 32-byte hex key and add to .env:
    ENCRYPTION_KEY=<output of: python -c "import secrets; print(secrets.token_hex(32))">

USAGE:
------
    from app.services.token_manager import encrypt_token, decrypt_token

    encrypted = encrypt_token("my_access_token_string")  # store this in DB
    plaintext = decrypt_token(encrypted)                  # use this to call Kite
"""

import base64
import os
from loguru import logger

from app.core.config import settings


def _get_key() -> bytes:
    """
    Derive the 32-byte AES key from ENCRYPTION_KEY env var.
    Falls back to a deterministic dev key if ENCRYPTION_KEY is empty
    (NEVER deploy to production without setting ENCRYPTION_KEY).
    """
    key_hex = settings.ENCRYPTION_KEY
    if not key_hex or len(key_hex) < 64:
        if settings.APP_ENV == "production":
            raise RuntimeError(
                "ENCRYPTION_KEY must be set in production. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        # Dev fallback — deterministic but not secret
        logger.warning(
            "⚠️  ENCRYPTION_KEY not set — using insecure dev key. "
            "Set ENCRYPTION_KEY in .env for production."
        )
        key_hex = "0" * 64  # 32 zero bytes — NOT for production
    return bytes.fromhex(key_hex[:64])


def encrypt_token(plaintext: str) -> str:
    """
    Encrypt a token string using AES-256-GCM.

    Returns a base64-encoded string of: nonce (12B) + ciphertext + tag (16B).
    Safe to store in a database VARCHAR column.

    Args:
        plaintext: The token string to encrypt (e.g. Zerodha access token)

    Returns:
        Base64-encoded encrypted blob
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = _get_key()
        nonce = os.urandom(12)  # 96-bit nonce — unique per encryption
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # Combine nonce + ciphertext+tag
        blob = nonce + ciphertext
        return base64.b64encode(blob).decode("ascii")

    except ImportError:
        logger.warning(
            "cryptography package not installed. "
            "Run: pip install cryptography. "
            "Storing token unencrypted as fallback."
        )
        # Graceful fallback: base64 only (not encrypted)
        return "UNENCRYPTED:" + base64.b64encode(plaintext.encode()).decode("ascii")

    except Exception as e:
        logger.error(f"Token encryption failed: {e}")
        raise


def decrypt_token(encrypted: str) -> str:
    """
    Decrypt a token encrypted by encrypt_token().

    Args:
        encrypted: Base64-encoded blob from encrypt_token()

    Returns:
        Plaintext token string

    Raises:
        ValueError: If the blob is corrupted or the key is wrong
    """
    if not encrypted:
        raise ValueError("Empty encrypted token")

    # Handle unencrypted fallback (dev mode without cryptography package)
    if encrypted.startswith("UNENCRYPTED:"):
        raw = encrypted[len("UNENCRYPTED:"):]
        return base64.b64decode(raw).decode("ascii")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = _get_key()
        blob = base64.b64decode(encrypted)
        nonce = blob[:12]
        ciphertext = blob[12:]
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")

    except ImportError:
        # Same fallback as encrypt
        raw = base64.b64decode(encrypted)
        return raw.decode("ascii")

    except Exception as e:
        logger.error(f"Token decryption failed: {e}")
        raise ValueError(f"Cannot decrypt token: {e}") from e


def is_encryption_configured() -> bool:
    """Return True if a real encryption key is configured."""
    key_hex = settings.ENCRYPTION_KEY
    return bool(key_hex) and len(key_hex) >= 64
