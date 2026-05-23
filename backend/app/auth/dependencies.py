"""
app/auth/dependencies.py
=========================
FastAPI dependency functions for authentication and authorization.

WHAT IS A FASTAPI DEPENDENCY?
-------------------------------
A dependency is a function FastAPI calls automatically before
your endpoint handler runs. You declare it with Depends().

Example — protecting an endpoint:
-----------------------------------
    from app.auth.dependencies import require_trader

    @router.post("/workflow/run")
    async def run_workflow(current_user: User = Depends(require_trader)):
        # current_user is guaranteed to be:
        #   - authenticated (valid JWT)
        #   - active (not deactivated)
        #   - has "trader" role or higher
        ...

DEPENDENCY CHAIN:
------------------
get_current_user        → extracts and validates JWT → returns User
    ↓ used by:
require_viewer          → any authenticated user (minimum access)
    ↓ used by:
require_trader          → must have trader/risk_manager/admin role
    ↓ used by:
require_risk_manager    → must have risk_manager/admin role
    ↓ used by:
require_admin           → must have admin role

ROLE HIERARCHY:
----------------
admin > risk_manager > trader > viewer

Each level includes all permissions of levels below it.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import decode_token
from app.db.postgres import AsyncSessionLocal
from app.db.models.user import User
from app.db.repos.user_repo import UserRepo


# ============================================================
# HTTP BEARER SCHEME
# ============================================================

# This tells FastAPI to expect the token in the HTTP header:
#   Authorization: Bearer eyJhbGci...
#
# auto_error=False means we handle the missing token ourselves
# with a cleaner error message than the default.

bearer_scheme = HTTPBearer(auto_error=False)


# ============================================================
# DATABASE SESSION DEPENDENCY
# ============================================================

async def get_db() -> AsyncSession:
    """
    Provides a database session to any endpoint that needs one.

    Usage:
    ------
        @router.get("/something")
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            result = await db.execute(...)

    The session is automatically closed after the request finishes,
    even if an exception occurs (the finally block handles cleanup).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ============================================================
# GET CURRENT USER  (core dependency)
# ============================================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Extract and validate the JWT from the Authorization header.
    Return the User object from the database.

    This dependency:
    1. Checks that the Authorization: Bearer header exists
    2. Decodes the JWT and verifies the signature and expiry
    3. Ensures the token type is "access" (not a refresh token)
    4. Loads the user from the database using the user_id in the token
    5. Checks the user account is still active (not deactivated)

    Raises HTTP 401 for any auth failure.
    The 401 response tells the frontend to redirect to the login page.

    You almost never call this directly — use the role-specific
    dependencies below (require_viewer, require_trader, etc.) instead.
    """

    # --------------------------------------------------------
    # STEP 1: Check token is present
    # --------------------------------------------------------
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please login to get a token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --------------------------------------------------------
    # STEP 2: Decode and verify JWT
    # --------------------------------------------------------
    payload = decode_token(credentials.credentials)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is invalid or expired. Please login again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --------------------------------------------------------
    # STEP 3: Ensure this is an access token, not a refresh token
    # --------------------------------------------------------
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type. Use your access token, not refresh token.",
        )

    # --------------------------------------------------------
    # STEP 4: Load user from database
    # --------------------------------------------------------
    user_id = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is malformed.",
        )

    user = await UserRepo.get_by_id(db, user_id)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found. Token may be for a deleted account.",
        )

    # --------------------------------------------------------
    # STEP 5: Check account is active
    # --------------------------------------------------------
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact an administrator.",
        )

    return user


# ============================================================
# ROLE-BASED DEPENDENCIES
# ============================================================

# ROLE HIERARCHY: admin > risk_manager > trader > viewer
# Each set includes all roles that are allowed at that level.

VIEWER_ROLES       = {"viewer", "trader", "risk_manager", "admin"}
TRADER_ROLES       = {"trader", "risk_manager", "admin"}
RISK_MANAGER_ROLES = {"risk_manager", "admin"}
ADMIN_ROLES        = {"admin"}


async def require_viewer(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Minimum access level — any authenticated active user.

    WHO CAN USE THIS:
      viewer, trader, risk_manager, admin

    USED ON ENDPOINTS LIKE:
      GET /workflow/{id}/status
      GET /trades
      GET /kill-switch/status
      WebSocket /market/stream
    """
    if current_user.role not in VIEWER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. Your role '{current_user.role}' cannot access this resource.",
        )
    return current_user


async def require_trader(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Trader level — can start agent workflow cycles.

    WHO CAN USE THIS:
      trader, risk_manager, admin

    USED ON ENDPOINTS LIKE:
      POST /workflow/run
    """
    if current_user.role not in TRADER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Role '{current_user.role}' cannot start workflow cycles. "
                f"Required: trader, risk_manager, or admin."
            ),
        )
    return current_user


async def require_risk_manager(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Risk manager level — can approve/reject HITL decisions
    and activate the kill switch.

    WHO CAN USE THIS:
      risk_manager, admin

    USED ON ENDPOINTS LIKE:
      POST /workflow/resume   (HITL approve/reject)
      POST /kill-switch/halt
      POST /kill-switch/resume
    """
    if current_user.role not in RISK_MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Role '{current_user.role}' cannot approve trades or use kill switch. "
                f"Required: risk_manager or admin."
            ),
        )
    return current_user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Admin level — full access including user management.

    WHO CAN USE THIS:
      admin only

    USED ON ENDPOINTS LIKE:
      POST /users             (create user)
      GET  /users             (list all users)
      PUT  /users/{id}/role   (change role)
      POST /users/{id}/deactivate
    """
    if current_user.role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Role '{current_user.role}' cannot manage users. "
                f"Required: admin."
            ),
        )
    return current_user