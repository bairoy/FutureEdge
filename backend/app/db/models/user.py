"""
app/db/models/user.py
======================
User table — one row per registered user.

TABLE RELATIONSHIPS:
---------------------
users (this table)
  └── trades          : one user → many trades  (user_id FK on trades)
  └── workflow_runs   : one user → many workflow runs (user_id FK on workflow_runs)
  └── refresh_tokens  : one user → many refresh tokens (user_id FK on refresh_tokens)

WHY THESE RELATIONSHIPS MATTER:
---------------------------------
Multi-user support means every trade and every workflow run
must be tied to a specific user.

  - "Show me MY trades"  → WHERE trades.user_id = current_user.id
  - "Resume MY workflow" → verify workflow_runs.user_id = current_user.id
  - "MY checkpoints"     → thread_id = f"{user_id}:{run_id}" (scoped per user)

ROLES (what each user can do):
---------------------------------
  viewer       → read-only: see dashboard, trades, agent results
  trader       → viewer + can trigger new agent cycles
  risk_manager → trader + can approve/reject HITL decisions
                         + can activate kill switch
  admin        → everything + manage users (create, deactivate, change roles)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):

    __tablename__ = "users"

    # --------------------------------------------------------
    # PRIMARY KEY
    # --------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="UUID primary key",
    )

    # --------------------------------------------------------
    # CREDENTIALS
    # --------------------------------------------------------

    # Email is the login identifier — must be globally unique
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
        comment="Login email — unique across all users",
    )

    # bcrypt hash — NEVER the plain password
    # If this column leaks, passwords still cannot be reversed
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="bcrypt hash of the password — never plain text",
    )

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    full_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name shown in the UI",
    )

    # --------------------------------------------------------
    # ROLE  (role-based access control)
    # --------------------------------------------------------

    # viewer | trader | risk_manager | admin
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="viewer",
        index=True,
        comment="viewer | trader | risk_manager | admin",
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    # Deactivated users cannot login.
    # We never hard-delete users — keeps audit trail intact.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="False = account disabled, cannot login",
    )

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When did this user last successfully login",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --------------------------------------------------------
    # ZERODHA CREDENTIALS  (per-user, encrypted at rest)
    # Each user stores their own Zerodha API credentials.
    # - zerodha_api_key:              plain text (not secret by itself)
    # - zerodha_api_secret:           encrypted with AES-256-GCM
    # - zerodha_access_token_encrypted: encrypted with AES-256-GCM
    #
    # Use app.services.token_manager.encrypt_token() / decrypt_token()
    # to read/write these fields.
    # --------------------------------------------------------

    zerodha_api_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default=None,
        comment="User's Zerodha API key (not secret by itself)",
    )

    zerodha_api_secret: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        default=None,
        comment="AES-256 encrypted Zerodha API secret",
    )

    zerodha_access_token_encrypted: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        default=None,
        comment="AES-256 encrypted Zerodha daily access token (expires 6 AM IST)",
    )

    # WhatsApp number for HITL and P&L alerts
    # Format: "+919876543210" (with country code)
    whatsapp_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default=None,
        comment="WhatsApp number for trade alerts e.g. +919876543210",
    )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # These let SQLAlchemy join tables automatically.
    # "lazy='dynamic'" means the query is not run until you
    # actually access the attribute.
    # --------------------------------------------------------

    # One user → many trades
    trades = relationship(
        "Trade",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # One user → many workflow runs
    workflow_runs = relationship(
        "WorkflowRun",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # One user → many refresh tokens (for multi-device login)
    refresh_tokens = relationship(
        "RefreshToken",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"User(id={self.id!r}, email={self.email!r}, "
            f"role={self.role!r}, active={self.is_active})"
        )