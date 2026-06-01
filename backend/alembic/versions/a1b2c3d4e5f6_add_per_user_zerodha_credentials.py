"""
Add per-user Zerodha credentials and WhatsApp number to users table.

Revision ID: a1b2c3d4e5f6
Revises: 73c6b37745bf
Create Date: 2026-05-30 10:00:00.000000

WHAT THIS ADDS:
----------------
- zerodha_api_key: User's Zerodha API key (plain text, not secret by itself)
- zerodha_api_secret: AES-256-GCM encrypted Zerodha API secret
- zerodha_access_token_encrypted: AES-256-GCM encrypted daily access token
- whatsapp_number: User's WhatsApp number for HITL and P&L alerts

WHY ALL NULLABLE:
------------------
Existing users don't have Zerodha credentials yet.
New columns are nullable so the migration is non-breaking.
Users add their credentials via the dashboard after migration.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "73c6b37745bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "zerodha_api_key",
            sa.String(length=100),
            nullable=True,
            comment="User's Zerodha API key (not secret by itself)",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "zerodha_api_secret",
            sa.String(length=512),
            nullable=True,
            comment="AES-256 encrypted Zerodha API secret",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "zerodha_access_token_encrypted",
            sa.String(length=1024),
            nullable=True,
            comment="AES-256 encrypted Zerodha daily access token",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "whatsapp_number",
            sa.String(length=20),
            nullable=True,
            comment="WhatsApp number for trade alerts e.g. +919876543210",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "whatsapp_number")
    op.drop_column("users", "zerodha_access_token_encrypted")
    op.drop_column("users", "zerodha_api_secret")
    op.drop_column("users", "zerodha_api_key")
