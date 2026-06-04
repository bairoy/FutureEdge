# README 11 — TELEGRAM BOT INTEGRATION & NGROK FOR WEBHOOKS

In the FutureEdge system, the **Human-in-the-Loop (HITL)** validation gate pauses high-risk trades and alerts the user. To make this experience seamless, we integrated a **Telegram Bot** that sends rich alerts directly to your phone, complete with interactive buttons to **Approve** or **Reject** trades instantly.

This guide explains the network mechanics behind this integration, why **ngrok** is required for local development, how the Telegram webhook API works, and provides a walkthrough of the integration code.

---

## 1. The Core Problem: Exposing Localhost to the Internet

### What is a Webhook?
A **webhook** is an HTTP callback. Instead of our server constantly asking Telegram if the user has clicked a button (polling), Telegram proactively sends an HTTP POST request to our server the moment an action occurs (push).

### The Localhost Barrier
When running FutureEdge locally:
* Your FastAPI application runs on `http://localhost:8000`.
* The Telegram servers live on the public internet.
* Telegram's servers have no way to reach `localhost:8000` because your local computer is behind a router (NAT) and a firewall, lacking a public, routable IP address.

```
┌──────────────────┐           (Cannot Route)           ┌─────────────────────┐
│ Telegram Servers │ ─────────────── ╳ ───────────────> │ Localhost App       │
│ (Public Internet)│                                    │ (http://localhost)  │
└──────────────────┘                                    └─────────────────────┘
```

---

## 2. What is ngrok and Why Did We Use It?

**ngrok** is a cross-platform application that exposes local development servers to the public internet over secure tunnels. 

### How ngrok Works
1. You run `ngrok http 8000` on your machine.
2. The ngrok client establishes a persistent connection to the cloud-based ngrok service.
3. The ngrok cloud service allocates a public, unique subdomain (e.g., `https://xxxx-xxxx.ngrok-free.app`).
4. When Telegram sends a webhook request to this public URL, ngrok forwards it down the tunnel to your local machine on port `8000`.

```
┌──────────────────┐       HTTPS Webhook       ┌─────────────────────┐
│ Telegram Servers │ ────────────────────────> │    ngrok Cloud      │
│ (Public Internet)│                           │   (Public URL)      │
└──────────────────┘                           └──────────┬──────────┘
                                                          │ Secure
                                                          │ Tunnel
                                                          ▼
┌──────────────────┐        FastAPI Port       ┌─────────────────────┐
│ Localhost App    │ <──────────────────────── │    ngrok Agent      │
│ (Port 8000)      │                           │   (Local Daemon)    │
└──────────────────┘                           └─────────────────────┘
```

---

## 3. Step-by-Step Setup Guide

### Phase A: Create your Telegram Bot
1. Open Telegram and search for **@BotFather**.
2. Type `/newbot` and follow the prompts to name your bot and choose a username.
3. BotFather will provide a **Bot Token** (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). Copy this.
4. Search for your bot in Telegram and click **Start / Send Message** to initialize the chat.

### Phase B: Get your Chat ID
To send alerts, the bot needs to know your personal chat ID:
1. Search for **@userinfobot** on Telegram.
2. Send any message to it, and it will reply with your `Id` (e.g., `987654321`). Copy this.

### Phase C: Configure the `.env` File
Add your tokens to the `.env` file at the root of the `futureedge` project:
```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ
TELEGRAM_CHAT_ID=987654321
```

### Phase D: Launch ngrok
Expose your backend server:
```bash
ngrok http 8000
```
Look for the forwarding address in your terminal:
```
Forwarding     https://9a2f-103-10-22-3.ngrok-free.app -> http://localhost:8000
```
> [!IMPORTANT]
> Keep this terminal tab running. If you restart ngrok, the URL will change (unless you have a paid plan with a static domain), meaning you must update your Telegram webhook URL.

### Phase E: Register the Webhook with Telegram
Tell Telegram where to send updates when the user interacts with the bot. Replace the placeholders with your actual Bot Token and ngrok URL:
```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=<NGROK_HTTPS_URL>/api/v1/telegram/webhook"
```

Example:
```bash
curl -X POST "https://api.telegram.org/bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ/setWebhook?url=https://9a2f-103-10-22-3.ngrok-free.app/api/v1/telegram/webhook"
```
On success, you will receive:
```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

---

## 4. Code Walkthrough: How the Webhook Works

The Telegram Bot webhook integration is composed of three parts in the codebase:
1. **Trigger Node**: The `HumanAgent` that pauses execution and triggers the alert.
2. **Notification Service**: The module that sends the HTML message and configures the callback buttons.
3. **Webhook Router**: The API endpoint that receives Telegram's callback and resumes the state machine.

### Part 1: Pausing the Workflow & Triggering the Notification
File: [human_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/human_agent.py)

When a trade is flagged as high-risk, the `human_agent` node is called. It calls our notification service and invokes LangGraph's `interrupt()` primitive:

```python
# 1. Dispatch the Telegram Alert with trade details
try:
    from app.services.telegram_service import send_hitl_trade_alert
    await send_hitl_trade_alert(proposal, run_id, is_paper=state.get("paper_trade", True))
except Exception as tg_err:
    logger.error(f"Failed to send Telegram HITL alert: {tg_err}")

# 2. Pause the workflow. Execution halts right here!
# LangGraph saves the state and waits for a resume command.
human_response = interrupt({
    "type": "human_review",
    "run_id": run_id,
    "symbol": proposal.symbol,
    "direction": proposal.direction,
    "size": proposal.size,
    "entry_price": proposal.entry_price,
    "agent_votes": [...]
})

# 3. Execution resumes here AFTER the user hits Approve/Reject in Telegram or the UI
decision = human_response.get("decision")
```

---

### Part 2: Sending the Message and Inline Callback Buttons
File: [telegram_service.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/services/telegram_service.py)

The `send_hitl_trade_alert` function formats the trade details into clean HTML. It attaches an `inline_keyboard` containing two buttons.
* The critical detail is `callback_data`. Each button is bound to data containing the action and the unique `run_id`:
  * **Approve** button: `approve:<run_id>`
  * **Reject** button: `reject:<run_id>`

```python
async def send_hitl_trade_alert(proposal: TradeProposal, run_id: str, is_paper: bool = True) -> int | None:
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
        f"<i>{proposal.llm_rationale}</i>\n"
        f"-----------------------------------------\n"
        f"<b>Run ID:</b> <code>{run_id}</code>\n"
        f"Please review and select an action below:"
    )

    # Attach interactive buttons with specific callback payloads
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
        # Save the message_id in Redis so we can edit it and remove buttons later
        await redis_client.setex(f"tg_msg:{run_id}", 86400, str(message_id))
        return message_id
    return None
```

---

### Part 3: Webhook Router (Resuming the State Machine)
File: [telegram_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/telegram_router.py)

When you tap **Approve** or **Reject** in Telegram:
1. Telegram sends a POST request to your ngrok URL (`/api/v1/telegram/webhook`).
2. Our router parses the action and `run_id` from `callback_data`.
3. It loads the paused LangGraph workflow and resumes it using `Command(resume=...)`.
4. It calls `answerCallbackQuery` (removes the loading spinner on Telegram).
5. It edits the original Telegram message to remove the buttons, preventing double-clicks.

```python
@router.post("/telegram/webhook", summary="Public webhook for Telegram Bot updates")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    callback_query = payload.get("callback_query")
    if not callback_query:
        return {"status": "ignored"}

    query_id = callback_query.get("id")
    callback_data = callback_query.get("data", "")  # e.g., "approve:c946d017"
    message = callback_query.get("message", {})
    message_id = message.get("message_id")
    original_text = message.get("text", "")

    action, run_id = callback_data.split(":", 1)
    decision = "APPROVE" if action == "approve" else "REJECT"

    # 1. Load the paused LangGraph workflow and resume it
    config = {"configurable": {"thread_id": run_id}}
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

    # 2. Update PostgreSQL database trade ledger status
    result = await db.execute(select(WorkflowRun).where(WorkflowRun.run_id == run_id))
    workflow_run = result.scalar_one_or_none()
    if workflow_run:
        workflow_run.status = "COMPLETED"
        await db.commit()

    # 3. Stop the loader spinner inside Telegram client
    await answer_callback_query(query_id, "Trade APPROVED!" if decision == "APPROVE" else "Trade REJECTED.")

    # 4. Edit the message on the chat to remove the Approve/Reject buttons
    status_icon = "✅" if decision == "APPROVE" else "❌"
    updated_text = (
        f"{original_text}\n\n"
        f"-----------------------------------------\n"
        f"<b>{status_icon} DECISION: {decision} (via Telegram Bot)</b>"
    )
    await edit_telegram_message(message_id, updated_text, reply_markup={"inline_keyboard": []})

    return {"status": "success", "run_id": run_id}
```

---

## 5. Troubleshooting & Debugging

### Inspect Tunnel Traffic via ngrok Dashboard
ngrok runs a local web inspection interface on your development machine.
* Open your browser and go to: **`http://localhost:4040`**
* You will see a list of all incoming POST requests sent by Telegram.
* You can inspect the request payload, check the response code from your FastAPI backend, and even **replay** the request to test your endpoint repeatedly without clicking the button again.

### Common Pitfalls
* **Stale Webhook URL:** If you restart your computer or restart ngrok, your public ngrok URL changes. If you forget to run `setWebhook` with the new URL, clicking buttons on Telegram will do nothing (or timeout with an hour-glass symbol).
* **Double Clicks:** If you press Approve, the webhook runs. If you press it again before the buttons disappear, it will hit `remove_telegram_buttons` as a "stale" request since the workflow run is no longer in `HITL_PENDING` status.
* **Server Timeouts:** Telegram expects webhooks to return an HTTP status code (like `200 OK`) within **10 seconds**. Because resuming the LangGraph workflow executes the trade synchronously, make sure your execution agent places the order quickly.

---

## 6. Securing Webhooks in Production

While ngrok is perfect for local development, in production you should expose your API securely:
1. **Validate Telegram IP Ranges:** Only allow incoming POST requests from Telegram's official IP subnets:
   - `149.154.160.0/20`
   - `91.108.4.0/22`
2. **Secret Path Token:** Use a random token in your webhook path that only you and Telegram know:
   `/api/v1/telegram/webhook/<SECRET_TOKEN>`
   This prevents arbitrary internet bots from sending fake callback payloads to your endpoint.
