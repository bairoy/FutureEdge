"""
app/scripts/create_admin.py
============================
Interactive script to create the first admin user.

Run this AFTER create_tables.py, BEFORE starting the app:

    docker compose exec backend python -m app.scripts.create_admin

HOW IT WORKS:
-------------
This script asks for email, password, and full name interactively,
then creates a user with role="admin" in the database.

The admin can then:
  1. Login via POST /auth/login
  2. Create other users via POST /users (admin only)
  3. Assign roles (viewer, trader, risk_manager, admin)

YOU NEED AT LEAST ONE ADMIN to use the system.
Without an admin, no one can create other users.

ALTERNATIVE — create admin non-interactively:
----------------------------------------------
    docker compose exec backend python -m app.scripts.create_admin \\
        --email admin@yourdomain.com \\
        --password StrongPass123 \\
        --name "Your Name"
"""

import asyncio
import sys
import argparse

from loguru import logger

from app.db.postgres import AsyncSessionLocal
from app.db.repos.user_repo import UserRepo

# Import all models so SQLAlchemy relationship resolution works
from app.db.models.user          import User
from app.db.models.refresh_token import RefreshToken
from app.db.models.workflow_run  import WorkflowRun
from app.db.models.trade         import Trade


async def create_admin(email: str, password: str, full_name: str) -> None:
    """
    Create an admin user in the database.

    Parameters:
    -----------
    email     : the admin's login email
    password  : plain text password (will be bcrypt hashed before saving)
    full_name : display name shown in the UI
    """

    # Validate password length
    if len(password) < 8:
        logger.error("Password must be at least 8 characters")
        sys.exit(1)

    async with AsyncSessionLocal() as session:

        # Check if any admin already exists
        existing = await UserRepo.get_by_email(session, email)

        if existing:
            logger.warning(f"User with email '{email}' already exists (role={existing.role})")
            logger.warning("No changes made.")
            return

        # Create the admin user
        user = await UserRepo.create_user(
            session   = session,
            email     = email,
            password  = password,
            full_name = full_name,
            role      = "admin",
        )

        logger.info("=" * 50)
        logger.info("Admin user created successfully!")
        logger.info(f"  Email     : {user.email}")
        logger.info(f"  Full Name : {user.full_name}")
        logger.info(f"  Role      : {user.role}")
        logger.info(f"  User ID   : {user.id}")
        logger.info("=" * 50)
        logger.info("")
        logger.info("You can now login at POST /auth/login")
        logger.info("Use this admin to create other users at POST /users")


def main() -> None:
    """
    Parse command-line arguments or prompt interactively.
    """

    parser = argparse.ArgumentParser(description="Create the first FutureEdge admin user")
    parser.add_argument("--email",    type=str, help="Admin email address")
    parser.add_argument("--password", type=str, help="Admin password (min 8 chars)")
    parser.add_argument("--name",     type=str, help="Admin full name")
    args = parser.parse_args()

    # Use CLI args if provided, otherwise prompt interactively
    email = args.email or input("Admin email: ").strip()
    name  = args.name  or input("Full name:   ").strip()

    if args.password:
        password = args.password
    else:
        import getpass
        password = getpass.getpass("Password (min 8 chars): ")
        confirm  = getpass.getpass("Confirm password:       ")

        if password != confirm:
            print("Passwords do not match. Exiting.")
            sys.exit(1)

    if not email or not name or not password:
        print("Email, name, and password are all required.")
        sys.exit(1)

    asyncio.run(create_admin(email, password, name))


if __name__ == "__main__":
    main()