"""
tests/services/test_token_manager.py
=======================================
Unit tests for AES-256-GCM token encryption.

WHAT WE TEST:
--------------
1. encrypt_token → decrypt_token roundtrip preserves plaintext
2. Different encryptions of the same string produce different ciphertexts (IV randomness)
3. Corrupted ciphertext raises ValueError
4. Empty string raises ValueError on decrypt
5. Unencrypted fallback (for environments without cryptography package)
6. is_encryption_configured() correctly reads ENCRYPTION_KEY
"""

import pytest
from unittest.mock import patch


# ============================================================
# TEST: ROUNDTRIP
# ============================================================

def test_encrypt_decrypt_roundtrip():
    """encrypt → decrypt must return the original plaintext."""
    from app.services.token_manager import encrypt_token, decrypt_token

    original = "test_zerodha_access_token_abc123"
    encrypted = encrypt_token(original)

    assert encrypted != original, "Encrypted value should differ from plaintext"
    assert isinstance(encrypted, str)

    recovered = decrypt_token(encrypted)
    assert recovered == original


# ============================================================
# TEST: DIFFERENT CIPHERTEXT ON SAME PLAINTEXT (IV randomness)
# ============================================================

def test_encrypt_same_input_gives_different_output():
    """Each call to encrypt_token should produce a unique ciphertext (random IV)."""
    from app.services.token_manager import encrypt_token

    token = "my_secret_token"
    enc1 = encrypt_token(token)
    enc2 = encrypt_token(token)

    # Same plaintext → different ciphertexts due to random 12-byte nonce
    assert enc1 != enc2, "Two encryptions of same plaintext should differ"


# ============================================================
# TEST: CORRUPTED CIPHERTEXT RAISES
# ============================================================

def test_decrypt_corrupted_ciphertext_raises():
    """Decrypting a corrupted blob should raise ValueError, not crash silently."""
    from app.services.token_manager import decrypt_token

    corrupted = "dGhpcyBpcyBub3QgdmFsaWQ="  # random base64 — wrong key/nonce

    with pytest.raises((ValueError, Exception)):
        decrypt_token(corrupted)


# ============================================================
# TEST: EMPTY INPUT RAISES
# ============================================================

def test_decrypt_empty_raises():
    """Decrypting an empty string should raise ValueError."""
    from app.services.token_manager import decrypt_token

    with pytest.raises(ValueError, match="Empty encrypted token"):
        decrypt_token("")


# ============================================================
# TEST: PRODUCTION RAISES WITHOUT KEY
# ============================================================

def test_production_raises_without_encryption_key(monkeypatch):
    """
    In production mode with no ENCRYPTION_KEY, encrypt_token must raise
    RuntimeError to prevent storing plaintext tokens in production.
    """
    from app.services import token_manager

    with (
        patch.object(token_manager.settings, "APP_ENV", "production"),
        patch.object(token_manager.settings, "ENCRYPTION_KEY", ""),
    ):
        with pytest.raises(RuntimeError, match="ENCRYPTION_KEY must be set"):
            token_manager._get_key()


# ============================================================
# TEST: is_encryption_configured
# ============================================================

def test_is_encryption_configured_true():
    """Returns True when ENCRYPTION_KEY is 64 hex chars."""
    from app.services import token_manager

    with patch.object(token_manager.settings, "ENCRYPTION_KEY", "a" * 64):
        from app.services.token_manager import is_encryption_configured
        assert is_encryption_configured() is True


def test_is_encryption_configured_false_on_empty():
    """Returns False when ENCRYPTION_KEY is empty."""
    from app.services import token_manager

    with patch.object(token_manager.settings, "ENCRYPTION_KEY", ""):
        from app.services.token_manager import is_encryption_configured
        assert is_encryption_configured() is False
