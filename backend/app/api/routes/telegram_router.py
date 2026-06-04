"""
app/api/routes/telegram_router.py
==================================
Telegram bot webhook endpoint for Personal HITL review.
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from langgraph.types import Command
from app.auth.dependencies import get_db
from app.db.models.workflow_run import WorkflowRun
from app.graph.runtime import get_workflow_graph
from app.core.config import settings
from app.services.telegram_service import edit_telegram_message
import httpx

router = APIRouter(prefix="/api/v1", tags=["Telegram"])

@router.post("/telegram/webhook", summary="Public webhook for Telegram Bot updates")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Handles inline button clicks from Telegram (Approve / Reject).
    Resumes the LangGraph workflow run corresponding to the callback's run_id.
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Telegram webhook received invalid JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.debug(f"Telegram Webhook Payload: {payload}")

    callback_query = payload.get("callback_query")
    if not callback_query:
        # Ignore non-callback messages (e.g. general text updates)
        return {"status": "ignored"}

    query_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    original_text = message.get("text", "")

    if not callback_data or ":" not in callback_data:
        return {"status": "invalid_callback_data"}

    # Callback data is formatted as "action:run_id" (e.g., "approve:a1b2c3d4")
    action, run_id = callback_data.split(":", 1)
    decision = "APPROVE" if action == "approve" else "REJECT"

    logger.info(f"Telegram Callback | run_id={run_id} | decision={decision}")

    # 1. Verify the workflow run exists in PostgreSQL
    result = await db.execute(
        select(WorkflowRun).where(WorkflowRun.run_id == run_id)
    )
    workflow_run = result.scalar_one_or_none()

    if not workflow_run:
        logger.error(f"Workflow run '{run_id}' not found in DB.")
        await answer_callback_query(query_id, f"Error: Workflow {run_id} not found.")
        return {"status": "not_found"}

    if workflow_run.status != "HITL_PENDING":
        logger.warning(f"Workflow run '{run_id}' is already in status {workflow_run.status}")
        await answer_callback_query(query_id, f"Workflow is already {workflow_run.status}")
        # Remove buttons since it's already completed
        await remove_telegram_buttons(message_id, original_text, f"Already {workflow_run.status}")
        return {"status": "already_completed"}

    # 2. Resume LangGraph Workflow Run
    config = {"configurable": {"thread_id": run_id}}
    try:
        graph = get_workflow_graph()
        state = await graph.ainvoke(
            Command(resume={
                "decision": decision,
                "notes": "Decided via Telegram Bot",
                "quantity": None,
                "position_rupees": None,
            }),
            config=config,
        )
    except Exception as e:
        logger.exception(f"Failed to resume workflow {run_id} via Telegram: {e}")
        await answer_callback_query(query_id, f"Failed to resume: {e}")
        return {"status": "resume_failed"}

    # 3. Update WorkflowRun status in PostgreSQL
    from datetime import datetime, timezone
    workflow_run.status = "COMPLETED"
    workflow_run.hitl_decided_at = datetime.now(timezone.utc)
    workflow_run.completed_at = datetime.now(timezone.utc)
    workflow_run.completed_nodes = state.get("completed_nodes", [])
    workflow_run.error_message = state.get("execution_error")
    await db.commit()

    # 4. Answer Callback Query to stop client loader spinner
    outcome_msg = "Trade APPROVED!" if decision == "APPROVE" else "Trade REJECTED."
    await answer_callback_query(query_id, outcome_msg)

    # 5. Edit Telegram message to remove buttons and show decision
    status_icon = "✅" if decision == "APPROVE" else "❌"
    updated_text = (
        f"{original_text}\n\n"
        f"-----------------------------------------\n"
        f"<b>{status_icon} DECISION: {decision} (via Telegram Bot)</b>"
    )
    # Pass empty reply_markup to remove buttons
    await edit_telegram_message(message_id, updated_text, reply_markup={"inline_keyboard": []})

    return {"status": "success", "run_id": run_id, "decision": decision}


async def answer_callback_query(callback_query_id: str, text: str):
    """
    Acknowledge the callback query so the user's client stops showing the loading spinner.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload = {
        "callback_query_id": callback_query_id,
        "text": text,
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=5.0)
    except Exception as e:
        logger.warning(f"Failed to answer callback query: {e}")

async def remove_telegram_buttons(message_id: int, original_text: str, status_msg: str):
    """
    Helper to clean up buttons on stale callback actions.
    """
    updated_text = (
        f"{original_text}\n\n"
        f"-----------------------------------------\n"
        f"<b>⚠️ Stale Alert: {status_msg}</b>"
    )
    await edit_telegram_message(message_id, updated_text, reply_markup={"inline_keyboard": []})
