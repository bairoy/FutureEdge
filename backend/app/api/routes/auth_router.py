"""
app/api/routes/auth_router.py
==============================
Authentication endpoints — login, refresh token, logout, get profile.

ENDPOINTS:
-----------
POST /auth/login
    Takes email + password.
    Returns access_token (30 min) + refresh_token (7 days).

POST /auth/refresh
    Takes a refresh_token.
    Returns a new access_token + new refresh_token (rotation).
    Old refresh_token is revoked.

POST /auth/logout
    Takes a refresh_token.
    Revokes it so it cannot be used again.
    The access_token will expire on its own in 30 min.

GET  /auth/me
    Returns the current user's profile.
    Requires: any authenticated user.

WHAT THE FRONTEND SHOULD DO:
------------------------------
1. Store access_token in memory (not localStorage — XSS risk)
2. Store refresh_token in an httpOnly cookie (safer than localStorage)
3. When access_token expires (401 response) → call /auth/refresh
4. On logout → call /auth/logout then clear both tokens
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from app.auth.password import verify_password
from app.auth.tokens import create_access_token, create_refresh_token, decode_token
from app.auth.dependencies import get_db, get_current_user, require_viewer
from app.db.models.user import User
from app.db.models.refresh_token import RefreshToken
from app.db.repos.user_repo import UserRepo
from app.api.dependencies.rate_limiter import rate_limit


router = APIRouter(prefix="/auth", tags=["Authentication"])


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class LoginRequest(BaseModel):
    email:    EmailStr   # validates email format automatically
    password: str


class TokenResponse(BaseModel):
    """Returned after successful login or token refresh."""
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int          # access token lifetime in seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class UserProfile(BaseModel):
    """Safe user data to return to the frontend — no password hash."""
    id:            str
    email:         str
    full_name:     str
    role:          str
    is_active:     bool
    last_login_at: datetime | None
    created_at:    datetime

    class Config:
        from_attributes = True   # allows creating from SQLAlchemy model


# ============================================================
# LOGIN
# ============================================================

@router.post(
    "/login",
    dependencies=[Depends(rate_limit(limit=5, window_seconds=60))],
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(
    request:     Request,
    body:        LoginRequest,
    db:          AsyncSession = Depends(get_db),
):
    """
    Authenticate a user and return JWT tokens.

    Steps:
    ------
    1. Look up user by email
    2. Verify the password against the stored bcrypt hash
    3. Check account is active
    4. Create access_token (30 min) and refresh_token (7 days)
    5. Store the refresh_token in the DB (for revocation support)
    6. Return both tokens

    Security notes:
    ---------------
    - We return the SAME error for "wrong email" and "wrong password"
      This prevents attackers from knowing which emails are registered.
    - We always run the password check even if the user is not found
      This prevents timing attacks (response time reveals if email exists).
    """

    # --------------------------------------------------------
    # LOOK UP USER
    # --------------------------------------------------------
    user = await UserRepo.get_by_email(db, body.email)

    # Run password check regardless of whether user exists.
    # This makes wrong-email and wrong-password take the same time.
    dummy_hash = "$2b$12$invalidhashfortimingnormalization000000000000000000000"
    stored_hash = user.hashed_password if user else dummy_hash

    password_correct = verify_password(body.password, stored_hash)

    if not user or not password_correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact an administrator.",
        )

    # --------------------------------------------------------
    # CREATE TOKENS
    # --------------------------------------------------------
    access_token = create_access_token(user.id, user.role)
    refresh_token_str, refresh_expires = create_refresh_token(user.id)

    # --------------------------------------------------------
    # STORE REFRESH TOKEN IN DB
    # --------------------------------------------------------
    # We store it so we can revoke it on logout or
    # when the user changes their password.

    client_ip   = request.client.host if request.client else None
    user_agent  = request.headers.get("user-agent", "unknown")[:255]

    db_token = RefreshToken(
        user_id     = user.id,
        token       = refresh_token_str,
        device_info = user_agent,
        ip_address  = client_ip,
        expires_at  = refresh_expires,
    )
    db.add(db_token)

    # Update last_login_at
    user.last_login_at = datetime.now(timezone.utc)

    await db.commit()

    logger.info(f"User logged in | email={user.email} | role={user.role} | ip={client_ip}")

    from app.core.config import settings

    return TokenResponse(
        access_token  = access_token,
        refresh_token = refresh_token_str,
        expires_in    = settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
    )


# ============================================================
# REFRESH TOKEN
# ============================================================

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Get a new access token using a refresh token",
)
async def refresh_token(
    body: RefreshRequest,
    db:   AsyncSession = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access token.

    TOKEN ROTATION:
    ---------------
    Every time this endpoint is called:
    1. Old refresh token is marked as revoked (is_revoked=True)
    2. A brand new refresh token is issued
    3. New access token is issued

    This way, if someone steals an old refresh token,
    it is already dead by the time they try to use it.

    The access token expires automatically (30 min) — we do not
    need to explicitly revoke it.
    """

    # --------------------------------------------------------
    # DECODE THE REFRESH TOKEN
    # --------------------------------------------------------
    payload = decode_token(body.refresh_token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token is invalid or expired. Please login again.",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )

    user_id = payload.get("sub")

    # --------------------------------------------------------
    # CHECK TOKEN EXISTS IN DB AND IS NOT REVOKED
    # --------------------------------------------------------
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token    == body.refresh_token,
            RefreshToken.user_id  == user_id,
            RefreshToken.is_revoked == False,
        )
    )
    db_token = result.scalar_one_or_none()

    if db_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found or already revoked. Please login again.",
        )

    # --------------------------------------------------------
    # LOAD USER AND CHECK STILL ACTIVE
    # --------------------------------------------------------
    user = await UserRepo.get_by_id(db, user_id)

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or deactivated.",
        )

    # --------------------------------------------------------
    # ROTATE: REVOKE OLD TOKEN, CREATE NEW TOKENS
    # --------------------------------------------------------
    db_token.is_revoked  = True
    db_token.last_used_at = datetime.now(timezone.utc)

    new_access_token             = create_access_token(user.id, user.role)
    new_refresh_token_str, expire = create_refresh_token(user.id)

    new_db_token = RefreshToken(
        user_id    = user.id,
        token      = new_refresh_token_str,
        device_info= db_token.device_info,
        ip_address = db_token.ip_address,
        expires_at = expire,
    )
    db.add(new_db_token)
    await db.commit()

    logger.info(f"Token refreshed | user={user.email}")

    from app.core.config import settings

    return TokenResponse(
        access_token  = new_access_token,
        refresh_token = new_refresh_token_str,
        expires_in    = settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
    )


# ============================================================
# LOGOUT
# ============================================================

@router.post(
    "/logout",
    summary="Logout — revoke the refresh token",
)
async def logout(
    body:         RefreshRequest,
    current_user: User = Depends(require_viewer),
    db:           AsyncSession = Depends(get_db),
):
    """
    Revoke the user's refresh token.

    The access token will expire naturally in 30 minutes.
    You cannot revoke a JWT access token (it is stateless).

    After calling this endpoint, the frontend should:
    1. Delete the access_token from memory
    2. Delete the refresh_token from the cookie
    3. Redirect to the login page
    """

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token   == body.refresh_token,
            RefreshToken.user_id == current_user.id,
        )
    )
    db_token = result.scalar_one_or_none()

    if db_token:
        db_token.is_revoked = True
        await db.commit()

    logger.info(f"User logged out | email={current_user.email}")

    return {"message": "Logged out successfully."}


# ============================================================
# GET CURRENT USER PROFILE
# ============================================================

@router.get(
    "/me",
    response_model=UserProfile,
    summary="Get the current logged-in user's profile",
)
async def get_me(
    current_user: User = Depends(require_viewer),
):
    """
    Returns the profile of the currently authenticated user.
    No sensitive data (hashed_password) is returned.

    The frontend calls this on app load to:
    - Show the user's name in the header
    - Know their role to show/hide buttons
    """
    return UserProfile.model_validate(current_user)