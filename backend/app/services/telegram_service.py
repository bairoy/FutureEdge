"""
app/services/telegram_service.py
==================================
Telegram Bot notification service for Personal HITL review.
"""

from loguru import logger
import httpx
from app.core.config import settings
from app.graph.state import TradeProposal

async def send_telegram_message(text: str, reply_markup: dict = None) -> dict | None:
    """
    Send a markdown message to the configured Telegram chat.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.warning("Telegram Bot Token or Chat ID not configured. Skipping alert.")
        return None

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                logger.info("Telegram message sent successfully.")
                return response.json()
            else:
                logger.error(f"Failed to send Telegram message: {response.text}")
                return None
    except Exception as e:
        logger.exception(f"Error sending Telegram message: {e}")
        return None

async def send_hitl_trade_alert(proposal: TradeProposal, run_id: str, is_paper: bool = True) -> int | None:
    """
    Format a TradeProposal into a premium HTML alert with inline callback buttons to Approve/Reject.
    """
    mode_str = "📝 PAPER TRADE" if is_paper else "🔥 LIVE ZERODHA TRADE"
    
    text = (
        f"<b>🔔 NEW TRADE PROPOSAL ({mode_str})</b>\n"
        f"-----------------------------------------\n"
        f"<b>Symbol:</b> {proposal.symbol}\n"
        f"<b>Direction:</b> {proposal.direction}\n"
        f"<b>Entry Price:</b> ₹{proposal.entry_price:.2f}\n"
        f"<b>Suggested Size:</b> ₹{proposal.size:.2f}\n"
        f"<b>Risk Score:</b> {proposal.risk_score:.2f}\n"
        f"-----------------------------------------\n"
        f"<b>Consensus Reasoning:</b>\n"
        f"<i>{proposal.llm_rationale or 'No consensus reasoning provided.'}</i>\n"
        f"-----------------------------------------\n"
        f"<b>Run ID:</b> <code>{run_id}</code>\n"
        f"Please review and select an action below:"
    )

    # Inline keyboard buttons to approve or reject
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{run_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{run_id}"}
            ]
        ]
    }

    result = await send_telegram_message(text, reply_markup=reply_markup)
    if result and result.get("ok"):
        message_id = result["result"]["message_id"]
        # Save message ID in redis for this run_id so we can update it later
        from app.db.redis import redis_client
        await redis_client.setex(f"tg_msg:{run_id}", 86400, str(message_id))
        return message_id
    return None

async def edit_telegram_message(message_id: int, text: str, reply_markup: dict = None) -> bool:
    """
    Edit an existing Telegram message by its message_id.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code == 200:
                logger.info(f"Telegram message {message_id} edited successfully.")
                return True
            else:
                logger.error(f"Failed to edit Telegram message: {response.text}")
                return False
    except Exception as e:
        logger.exception(f"Error editing Telegram message: {e}")
        return False
