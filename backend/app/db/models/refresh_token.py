"""
app/db/models/refresh_token.py
================================
RefreshToken table — stores refresh tokens for multi-device login.

WHY STORE REFRESH TOKENS IN THE DATABASE?
------------------------------------------
Access tokens (30 min) are stateless — we verify them by
checking the JWT signature, no DB needed.

Refresh tokens (7 days) MUST be stored in the DB because:

1. REVOCATION: If a user logs out, changes their password,
   or their account is deactivated, we need to invalidate
   their refresh token immediately.
   Pure JWT cannot be revoked without a DB lookup.

2. MULTI-DEVICE: A user might be logged in on their phone,
   laptop, and tablet at the same time. Each device gets its
   own refresh token. Logging out on one device should only
   invalidate that device's token, not all devices.

3. ROTATION: Every time a refresh token is used to get a
   new access token, we issue a NEW refresh token and
   invalidate the old one. This limits the damage if a
   refresh token is stolen.

HOW TOKEN ROTATION WORKS:
---------------------------
1. User logs in → get access_token (30min) + refresh_token (7 days)
2. Access token expires → user sends refresh_token to /auth/refresh
3. We verify refresh_token is in DB and not expired/revoked
4. We issue NEW access_token + NEW refresh_token
5. We mark the OLD refresh_token as is_revoked=True
6. This way, if someone steals an old refresh token, it is already dead
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RefreshToken(Base):

    __tablename__ = "refresh_tokens"

    # --------------------------------------------------------
    # IDENTITY
    # --------------------------------------------------------

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    # Which user owns this token
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to users.id",
    )

    # --------------------------------------------------------
    # TOKEN
    # --------------------------------------------------------

    # The actual JWT refresh token string
    # We store it so we can verify it exists and is not revoked
    token: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        unique=True,
        index=True,
        comment="The JWT refresh token string",
    )

    # --------------------------------------------------------
    # DEVICE INFO  (optional but useful for "manage sessions" UI)
    # --------------------------------------------------------

    # Which browser/device issued this token
    # Example: "Chrome on Windows", "FutureEdge Mobile App"
    device_info: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Browser or device name for the sessions UI",
    )

    # IP address at time of login (for security audit)
    ip_address: Mapped[str | None] = mapped_column(
        String(45),     # 45 chars covers IPv6 addresses
        nullable=True,
    )

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    # True once the token has been used to get a new access token.
    # A revoked token cannot be used again — prevents replay attacks.
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="True = token has been used or manually revoked",
    )

    # --------------------------------------------------------
    # TIMESTAMPS
    # --------------------------------------------------------

    # When this refresh token expires (7 days from creation)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When this refresh token expires",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return (
            f"RefreshToken(user_id={self.user_id!r}, "
            f"revoked={self.is_revoked}, expires={self.expires_at})"
        )