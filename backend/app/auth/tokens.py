"""
app/auth/tokens.py
===================
JWT token creation and verification.

HOW JWT AUTH WORKS IN FUTUREEDGE:
-----------------------------------
1. POST /auth/login   → verify password → return access_token + refresh_token
2. Every API request  → send "Authorization: Bearer <access_token>" header
3. FastAPI dependency → decodes JWT → gives you the current user object
4. POST /auth/refresh → send refresh_token → get new access_token + refresh_token
5. POST /auth/logout  → revoke refresh_token in DB

ACCESS TOKEN  (30 minutes, stateless):
  - Short-lived so stolen tokens expire quickly
  - Verified by checking JWT signature — NO database lookup needed
  - Contains: user_id, role, expiry

REFRESH TOKEN  (7 days, stored in DB):
  - Long-lived so users don't have to re-login every 30 minutes
  - Verified by checking DB — allows revocation
  - Contains: user_id, expiry


"""

from datetime import datetime, timedelta, timezone

import jwt
from loguru import logger

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(user_id: str, role: str) -> str:
    """
    Create a short-lived JWT access token (default 30 minutes).

    Payload contains:
      sub  : user_id  (standard JWT "subject" claim)
      role : the user's role so every endpoint can check permissions
      type : "access" so we reject refresh tokens used as access tokens
      exp  : expiry time
      iat  : issued-at time
    """
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES)

    return jwt.encode(
        {
            "sub":  user_id,
            "role": role,
            "type": "access",
            "iat":  now,
            "exp":  expire,
        },
        settings.JWT_SECRET_KEY,
        algorithm=ALGORITHM,
    )


def create_refresh_token(user_id: str) -> tuple[str, datetime]:
    """
    Create a long-lived JWT refresh token (default 7 days).

    Returns both the encoded token string AND the expiry datetime.
    The expiry datetime is stored in the refresh_tokens DB table
    so we can clean up expired tokens without decoding them.

    Returns:
    --------
    (token_string, expires_at_datetime)
    """
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS)

    token = jwt.encode(
        {
            "sub":  user_id,
            "type": "refresh",
            "iat":  now,
            "exp":  expire,
        },
        settings.JWT_SECRET_KEY,
        algorithm=ALGORITHM,
    )

    return token, expire


def decode_token(token: str) -> dict | None:
    """
    Decode and verify a JWT token.

    Automatically checks:
    1. Signature — was it signed with our secret key?
    2. Expiry    — has the exp claim passed?

    Returns the payload dict on success, None on any failure.
    Never raises — always returns None for invalid tokens.
    """
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT invalid: {e}")
        return None