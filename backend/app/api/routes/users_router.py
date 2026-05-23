"""
app/api/routes/users_router.py
================================
User management endpoints — admin only.

ENDPOINTS:
-----------
POST /users                    → create a new user (admin only)
GET  /users                    → list all users (admin only)
GET  /users/{user_id}          → get one user's profile (admin only)
PUT  /users/{user_id}/role     → change a user's role (admin only)
POST /users/{user_id}/deactivate → disable a user account (admin only)
POST /users/{user_id}/activate   → re-enable a user account (admin only)

WHY ADMIN ONLY?
----------------
Managing users means creating accounts, changing roles, and
deactivating people. These actions have direct financial
consequences in a trading system — a rogue admin could
give themselves risk_manager access to approve their own trades.

In production you should add extra safeguards like:
  - Require a second admin to confirm role changes
  - Email the affected user when their role changes
  - Log all admin actions to an immutable audit log
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.auth.dependencies import get_db, require_admin
from app.db.models.user import User
from app.db.repos.user_repo import UserRepo


router = APIRouter(prefix="/users", tags=["User Management"])


# ============================================================
# REQUEST / RESPONSE MODELS
# ============================================================

class CreateUserRequest(BaseModel):
    email:     EmailStr
    password:  str
    full_name: str
    role:      str = "viewer"    # default to minimum access


class UpdateRoleRequest(BaseModel):
    role: str    # viewer | trader | risk_manager | admin


class UserResponse(BaseModel):
    """Safe user data — no hashed_password."""
    id:            str
    email:         str
    full_name:     str
    role:          str
    is_active:     bool
    last_login_at: str | None
    created_at:    str

    class Config:
        from_attributes = True


# ============================================================
# CREATE USER
# ============================================================

@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (admin only)",
)
async def create_user(
    body:         CreateUserRequest,
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    """
    Create a new user account.

    The admin chooses the role at creation time.
    The new user can login immediately with the provided credentials.

    Roles:
      viewer       → read-only dashboard access
      trader       → can start agent workflow cycles
      risk_manager → can approve/reject HITL decisions
      admin        → full access including user management
    """

    valid_roles = {"viewer", "trader", "risk_manager", "admin"}
    if body.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role '{body.role}'. Choose from: {sorted(valid_roles)}",
        )

    if len(body.password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters.",
        )

    try:
        user = await UserRepo.create_user(
            session   = db,
            email     = body.email,
            password  = body.password,
            full_name = body.full_name,
            role      = body.role,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    logger.info(
        f"Admin {current_user.email} created user "
        f"{user.email} with role={user.role}"
    )

    return _user_to_response(user)


# ============================================================
# LIST ALL USERS
# ============================================================

@router.get(
    "",
    response_model=list[UserResponse],
    summary="List all users (admin only)",
)
async def list_users(
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    """
    Returns a list of all user accounts.
    Shows inactive/deactivated accounts too (for audit purposes).
    """

    users = await UserRepo.get_all_users(db)
    return [_user_to_response(u) for u in users]


# ============================================================
# GET ONE USER
# ============================================================

@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a user's profile (admin only)",
)
async def get_user(
    user_id:      str,
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    user = await UserRepo.get_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found.",
        )

    return _user_to_response(user)


# ============================================================
# CHANGE ROLE
# ============================================================

@router.put(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Change a user's role (admin only)",
)
async def change_role(
    user_id:      str,
    body:         UpdateRoleRequest,
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    """
    Change the role of any user.

    Safety: an admin cannot downgrade their own role.
    This prevents accidentally locking yourself out of the system.
    """

    # Prevent admin from removing their own admin access
    if user_id == current_user.id and body.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own admin role. Ask another admin.",
        )

    try:
        user = await UserRepo.update_role(db, user_id, body.role)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found.",
        )

    logger.info(
        f"Admin {current_user.email} changed role for "
        f"{user.email} → {body.role}"
    )

    return _user_to_response(user)


# ============================================================
# DEACTIVATE USER
# ============================================================

@router.post(
    "/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user account (admin only)",
)
async def deactivate_user(
    user_id:      str,
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    """
    Disable a user account. The user cannot login after this.

    We never delete users — deactivating keeps the full audit trail
    so you can always see which trades were placed by which user.

    Safety: an admin cannot deactivate themselves.
    """

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    user = await UserRepo.deactivate_user(db, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found.",
        )

    return _user_to_response(user)


# ============================================================
# ACTIVATE USER
# ============================================================

@router.post(
    "/{user_id}/activate",
    response_model=UserResponse,
    summary="Re-activate a deactivated user account (admin only)",
)
async def activate_user(
    user_id:      str,
    current_user: User = Depends(require_admin),
    db:           AsyncSession = Depends(get_db),
):
    """Re-enable a deactivated user account."""

    user = await UserRepo.get_by_id(db, user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found.",
        )

    user.is_active = True
    await db.commit()
    await db.refresh(user)

    logger.info(
        f"Admin {current_user.email} activated user {user.email}"
    )

    return _user_to_response(user)


# ============================================================
# HELPER
# ============================================================

def _user_to_response(user: User) -> UserResponse:
    """Convert SQLAlchemy User model to response dict."""
    return UserResponse(
        id            = user.id,
        email         = user.email,
        full_name     = user.full_name,
        role          = user.role,
        is_active     = user.is_active,
        last_login_at = user.last_login_at.isoformat() if user.last_login_at else None,
        created_at    = user.created_at.isoformat(),
    )