"""
app/services/whatsapp_service.py
==================================
WhatsApp alert service — PLACEHOLDER (currently disabled).

All trade alerts and notifications are displayed on the dashboard.
WhatsApp delivery is disabled.
"""

from loguru import logger

async def _send_whatsapp(to: str, body: str) -> bool:
    """No-op send function."""
    logger.info(f"WhatsApp alert disabled. Message to {to}: {body[:80]}...")
    return True

async def send_hitl_alert(
    to_number: str,
    symbol: str,
    direction: str,
    risk_score: float,
    position_inr: float,
    rationale: str,
    run_id: str,
    dashboard_url: str = "",
) -> bool:
    """No-op HITL alert."""
    logger.info(f"WhatsApp HITL alert skipped for {symbol} {direction} (displays on dashboard).")
    return True

async def send_daily_pnl_report(
    to_number: str,
    date_str: str,
    total_trades: int,
    wins: int,
    losses: int,
    realized_pnl: float,
    best_trade: dict | None = None,
    worst_trade: dict | None = None,
) -> bool:
    """No-op daily P&L report."""
    logger.info(f"WhatsApp daily P&L report skipped for {date_str} (displays on dashboard).")
    return True

async def send_loss_cap_alert(
    to_number: str,
    daily_loss_inr: float,
    daily_loss_pct: float,
    cap_pct: float,
) -> bool:
    """No-op loss cap alert."""
    logger.warning("WhatsApp Daily loss cap alert skipped (displays on dashboard).")
    return True

async def send_token_refresh_reminder(
    to_number: str,
    date_str: str,
    login_url: str = "https://kite.zerodha.com",
) -> bool:
    """No-op token refresh reminder."""
    logger.info("WhatsApp token refresh reminder skipped.")
    return True
