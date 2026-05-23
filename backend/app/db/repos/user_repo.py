"""
app/db/repos/user_repo.py
==========================
All database read/write operations for the User table.

USAGE EXAMPLES:
---------------
    from app.db.repos.user_repo import UserRepo
    from app.db.postgres import AsyncSessionLocal

    # Create a new user
    async with AsyncSessionLocal() as session:
        user = await UserRepo.create_user(
            session,
            email="baiju@futureedge.com",
            password="StrongPass123",
            full_name="Baiju Yadav",
            role="admin",
        )

    # Find user by email (used during login)
    async with AsyncSessionLocal() as session:
        user = await UserRepo.get_by_email(session, "baiju@futureedge.com")
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from app.db.models.user import User
from app.auth.password import hash_password


class UserRepo:
    """
    All DB operations for the users table.
    All methods are static — no need to instantiate.
    """

    # --------------------------------------------------------
    # CREATE USER
    # --------------------------------------------------------

    @staticmethod
    async def create_user(
        session:   AsyncSession,
        email:     str,
        password:  str,         # plain text — hashed before saving
        full_name: str,
        role:      str = "viewer",
    ) -> User:
        """
        Create a new user account.

        The plain-text password is hashed with bcrypt before
        being stored. The original password is never saved.

        Raises ValueError if email already exists.
        """

        # Check if email is already taken
        existing = await UserRepo.get_by_email(session, email)
        if existing:
            raise ValueError(f"Email '{email}' is already registered")

        user = User(
            email           = email.lower().strip(),
            hashed_password = hash_password(password),
            full_name       = full_name.strip(),
            role            = role,
            is_active       = True,
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info(
            f"User created | email={user.email} | role={user.role}"
        )

        return user

    # --------------------------------------------------------
    # GET BY EMAIL  (used during login)
    # --------------------------------------------------------

    @staticmethod
    async def get_by_email(
        session: AsyncSession,
        email:   str,
    ) -> User | None:
        """
        Find a user by their email address.
        Returns None if not found.
        Used by the login endpoint to look up the user.
        """

        result = await session.execute(
            select(User).where(User.email == email.lower().strip())
        )
        return result.scalar_one_or_none()

    # --------------------------------------------------------
    # GET BY ID  (used to validate JWT tokens)
    # --------------------------------------------------------

    @staticmethod
    async def get_by_id(
        session:  AsyncSession,
        user_id:  str,
    ) -> User | None:
        """
        Find a user by their UUID.
        Called on every protected request to verify the token
        still points to a valid, active account.
        """

        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    # --------------------------------------------------------
    # GET ALL USERS  (admin only)
    # --------------------------------------------------------

    @staticmethod
    async def get_all_users(session: AsyncSession) -> list[User]:
        """
        Fetch all users. Only callable by admin role.
        """

        result = await session.execute(
            select(User).order_by(User.created_at.desc())
        )
        return list(result.scalars().all())

    # --------------------------------------------------------
    # UPDATE LAST LOGIN
    # --------------------------------------------------------

    @staticmethod
    async def update_last_login(
        session: AsyncSession,
        user_id: str,
    ) -> None:
        """
        Record when this user last logged in.
        Called after a successful login.
        """

        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user:
            user.last_login_at = datetime.now(timezone.utc)
            await session.commit()

    # --------------------------------------------------------
    # UPDATE ROLE  (admin only)
    # --------------------------------------------------------

    @staticmethod
    async def update_role(
        session:  AsyncSession,
        user_id:  str,
        new_role: str,
    ) -> User | None:
        """
        Change a user's role.
        Only admins can call this.
        """

        valid_roles = {"viewer", "trader", "risk_manager", "admin"}
        if new_role not in valid_roles:
            raise ValueError(f"Invalid role: {new_role}. Must be one of {valid_roles}")

        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user:
            old_role   = user.role
            user.role  = new_role
            await session.commit()
            await session.refresh(user)

            logger.info(
                f"Role updated | user={user.email} | "
                f"{old_role} -> {new_role}"
            )

        return user

    # --------------------------------------------------------
    # DEACTIVATE USER  (admin only — soft delete)
    # --------------------------------------------------------

    @staticmethod
    async def deactivate_user(
        session:  AsyncSession,
        user_id:  str,
    ) -> User | None:
        """
        Disable a user account.
        The user cannot login after this.
        We never delete users — deactivating keeps the audit trail.
        """

        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user:
            user.is_active = False
            await session.commit()
            await session.refresh(user)

            logger.warning(f"User deactivated | email={user.email}")

        return user