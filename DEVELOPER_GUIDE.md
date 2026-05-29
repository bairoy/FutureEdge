# FutureEdge — Advanced Backend Engineering & Quantitative Guide

This guide provides a deep-dive reference for the FutureEdge backend codebase. It details the system architecture, mathematical formulas, state machine loops, database sessions, and execution layers.

---

## 1. LangGraph State Machine & Checkpointer Lifecycle

FutureEdge manages the execution of its multi-agent system using **LangGraph**. A state machine is essential for trading because decisions must follow a strict, auditable sequence, and must support **Human-In-The-Loop (HITL)** manual approvals for high-risk trades.

### The Execution Graph Structure
The graph is compiled in `backend/app/graph/builder.py` and registered in `backend/app/graph/runtime.py`. It comprises nodes, static edges, and conditional routing:

```
                  ┌───────────────┐
                  │     START     │
                  └───────┬───────┘
                          │
                ┌─────────▼─────────┐
                │   regime_agent    │
                └─────────┬─────────┘
                          │
      ┌───────────────────┼───────────────────┬───────────────────┐
┌─────▼──────┐      ┌─────▼──────┐      ┌─────▼──────┐      ┌─────▼──────┐
│signal_agent│      │sentim_agent│      │ risk_agent │      │portf_agent │
└─────┬──────┘      └─────┬──────┘      └─────┬──────┘      └─────┬──────┘
      │                   │                   │                   │
      └───────────────────┼───────────────────┴───────────────────┘
                          │
                ┌─────────▼─────────┐
                │   orchestrator    │
                └─────────┬─────────┘
                          │
             [should_human_review?]
             /                    \
     {"execute"}                {"human_review"}
           /                        \
┌─────────▼─────────┐      ┌─────────▼─────────┐
│     execution     │      │   human_review    │
└─────────┬─────────┘      └─────────┬─────────┘
          │                          │
          │                (Awaits Approval Signal)
          │                          │
          └──────────────────────────┴─────> [ END ]
```

### Async Checkpointer Lifecycle (`app/graph/runtime.py`)
To pause and resume execution during human review, LangGraph requires a persistent state checkpointer. We use `AsyncPostgresSaver` backed by PostgreSQL:

1.  **State Save (Checkpointing)**:
    When the Orchestrator determines a trade requires human review, the node sets `hitl_status = "PENDING"` and `hitl_required = True`. The graph reaches the `human_review` node and halts execution. LangGraph automatically serializes the entire `AgentState` object (including logs, votes, and context) and writes it to the database table `checkpoint_writes` under the current thread's unique `thread_id`.
2.  **FastAPI REST Hook (`workflow_router.py`)**:
    The workflow run finishes, returning the generated `thread_id` to the frontend dashboard. The API server returns a `200 OK` status with `hitl_status: "PENDING"`.
3.  **State Resume (HITL Action)**:
    When the risk manager clicks "Approve" or "Reject" on the dashboard, it calls:
    `POST /api/v1/workflow/resume`
    The backend fetches the thread state using the `thread_id`:
    ```python
    # Load the exact state at the point of suspension
    state_info = await workflow_graph.aget_state(config)
    ```
    It updates the state with the human decision (`APPROVED` or `REJECTED`) and calls `ainvoke` again, resuming execution exactly where it left off (passing the state to the `execution` node).

---

## 2. Quantitative Calculations & Mathematical Models

### 1. Market Regime Classification Math (`regime_agent.py`)
The `RegimeAgent` uses rolling calculation windows on OHLCV candles to determine the market state.

#### Average Directional Index (ADX)
ADX measures the strength of a trend. First, we compute the Directional Movement ($+DM$, $-DM$) and True Range ($TR$):

$$TR = \max \left( H_t - L_t, \, |H_t - C_{t-1}|, \, |L_t - C_{t-1}| \right)$$

$$\text{ATR}_{14} = \text{Rolling Mean of } TR \text{ over 14 periods}$$

$$\text{If } (H_t - H_{t-1}) > (L_{t-1} - L_t) \text{ and } (H_t - H_{t-1}) > 0 \implies +DM = H_t - H_{t-1}, \text{ else } 0$$

$$\text{If } (L_{t-1} - L_t) > (H_t - H_{t-1}) \text{ and } (L_{t-1} - L_t) > 0 \implies -DM = L_{t-1} - L_t, \text{ else } 0$$

$$+DI = 100 \times \frac{\text{Rolling Mean}(+DM, 14)}{\text{ATR}_{14}}$$

$$-DI = 100 \times \frac{\text{Rolling Mean}(-DM, 14)}{\text{ATR}_{14}}$$

$$\text{DX} = 100 \times \frac{|+DI - -DI|}{+DI + -DI}$$

$$\text{ADX}_{14} = \text{Rolling Mean of } \text{DX} \text{ over 14 periods}$$

#### EMA Slope
EMA is calculated as:

$$\text{EMA}_t = \left( C_t \times \frac{2}{N+1} \right) + \left( \text{EMA}_{t-1} \times \left(1 - \frac{2}{N+1}\right) \right)$$

$$\text{Slope} = \text{EMA}_{20, t} - \text{EMA}_{20, t-3}$$

A positive slope greater than $0.01\%$ of the asset's price indicates strong upward momentum.

---

### 2. Relative Strength Index (RSI) (`indicator_cache.py`)
RSI measures price momentum:

$$\text{RSI} = 100 - \frac{100}{1 + RS}$$

Where $RS = \frac{\text{Average Gain}}{\text{Average Loss}}$ over the last 14 candles.

```python
# pandas implementation inside app/data/indicator_cache.py
delta = prices.diff()
gain  = delta.where(delta > 0, 0).rolling(window=14).mean()
loss  = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
rs  = gain / loss.replace(0, 1e-10)
rsi = 100 - (100 / (1 + rs.iloc[-1]))
```

---

### 3. Kelly Sizing Probability Estimation (`risk_agent.py`)
The optimal leverage or trade size is computed using your account's historical win rate and profit ratio:

$$f^* = \frac{p \cdot b - (1 - p)}{b}$$

Where:
*   $p = \frac{\text{Number of Profitable Trades}}{\text{Total Closed Trades}}$
*   $b = \frac{\text{Average realized P\&L of winning trades}}{\text{Average absolute realized P\&L of losing trades}}$

#### Code Implementation:
```python
# Extracted from app/agents/risk_agent.py
win_rate = stats["win_rate"]  # e.g., 0.55 (55%)
avg_win  = stats["avg_win"]   # e.g., ₹1,500
avg_loss = stats["avg_loss"]  # e.g., ₹1,000

b = avg_win / avg_loss        # Reward-to-Risk ratio (1.5)
q = 1.0 - win_rate            # Loss rate (0.45)

full_kelly = (win_rate * b - q) / b  # (0.55 * 1.5 - 0.45) / 1.5 = 0.25
half_kelly = full_kelly / 2.0        # Half-Kelly buffer = 0.125 (12.5% size)
result = max(0.02, min(half_kelly, 0.25))  # Clamp between 2% and 25%
```

---

## 3. Zerodha Kite Integration & Order Execution Flow

FutureEdge communicates with the Zerodha Kite API to fetch live tick data and place orders.

### Session Lifecycle (`app/brokers/zerodha.py`)
Access tokens expire at 6 AM IST every day.
1.  **Callback Hook**: The user logs in via Zerodha, redirecting to `POST /api/v1/zerodha/callback?request_token=...`.
2.  **Session Generation**:
    ```python
    # app/api/routes/zerodha_router.py
    session_data = kite.generate_session(request_token, api_secret=settings.ZERODHA_API_SECRET)
    access_token = session_data["access_token"]
    ```
3.  **Local Storage JSON**: The token is saved in `backend/broker_token.json` rather than `.env` to prevent Docker from restarting.
4.  **Client Initialization**:
    ```python
    # app/brokers/zerodha.py
    # Initialize connection using the token
    kite.set_access_token(access_token)
    ```

### Live Ticks Websocket Loop (`KiteTicker`)
To get live price ticks, we run a background loop using Zerodha's `KiteTicker` library:
1.  **WebSocket Connection**: Establishes a persistent connection to Zerodha's tick servers.
2.  **Subscribe**: Subscribes to the specific instrument token (e.g. `256265` for NIFTY 50).
3.  **On Ticks Event handler**:
    ```python
    def on_ticks(ws, ticks):
        for tick in ticks:
            # Extract LTP (Last Traded Price)
            ltp = tick["last_price"]
            # Publish tick payload to Redis Stream
            redis_client.xadd(STREAM_TICKS, {"price": ltp, "symbol": symbol})
    ```
4.  **WebSocket Stream (`market_router.py`)**: A FastAPI WebSocket endpoint reads ticks from the Redis Stream and pushes them to the React frontend dashboard in real-time.

---

## 4. Database Architecture & Scoped Async Sessions

FutureEdge uses **SQLAlchemy 2.0** with async database sessions. 

### Database Schema Map
The database contains four core tables structured around multi-user support:

```
 ┌────────────────────────────────┐
 │             users              │
 ├────────────────────────────────┤
 │ id (PK)                        │◄──────┐
 │ email                          │       │
 │ hashed_password                │       │
 │ role (ADMIN, TRADER, VIEWER)   │       │
 └────────────────────────────────┘       │
                 │                        │
        ┌────────┴────────┬───────────────┼──────────────┐
        ▼                 ▼               │              ▼
 ┌──────────────┐  ┌──────────────┐       │       ┌──────────────┐
 │refresh_tokens│  │workflow_runs │       │       │    trades    │
 ├──────────────┤  ├──────────────┤       │       ├──────────────┤
 │ id (PK)      │  │ id (PK)      │       │       │ id (PK)      │
 │ user_id (FK) │  │ user_id (FK) │       │       │ user_id (FK) │
 │ token        │  │ status       │◄──┐   └───────│ status       │
 └──────────────┘  └──────────────┘   │           │ workflow_id  │
                                      └───────────│ (FK)         │
                                                  └──────────────┘
```

### Managing Scoped Async Sessions (`backend/app/db/postgres.py`)
To prevent connection leaks and ensure thread safety in asynchronous Python, we use a scoped session factory context manager:

```python
# Definition
AsyncEngine = create_async_engine(DATABASE_URL, pool_size=20, max_overflow=10)
AsyncSessionLocal = async_sessionmaker(AsyncEngine, expire_on_commit=False)

# Correct Usage Pattern inside Nodes/Routes
async def get_user_data(user_id: str):
    # Context manager automatically returns connection to pool when block exits
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        return user
```
*Note: Always use `expire_on_commit=False` in async context so that attributes accessed on ORM objects after committing do not trigger unexpected lazy-loading network calls.*

---

## 5. Model Fine-Tuning Pipeline: Step-by-Step

Our custom model pipeline allows you to train Llama-3 to write trading rationales locally.

### 1. Generating Training Targets (`app/scripts/bootstrap_dataset.py`)
We run a simulation of our agents on historical candles. If the consensus generates a trade setup, we capture the data.
We use **Teacher Distillation** to construct the target rationale:
*   We send the raw indicator values to `gpt-4o-mini` with a prompt instructing it to write a 2-3 sentence analysis.
*   We save the input prompt and the distilled response as a training example in `finetuning_dataset.jsonl`.

### 2. Supervised Fine-Tuning (SFT) using LoRA
Instead of training all 8 billion parameters (which requires gigabytes of VRAM), we freeze the base model and train a small, lightweight set of parameters called **adapters** (LoRA):
*   We insert rank decomposition matrices (where $r=16$) into the attention layers (`q_proj`, `v_proj`).
*   Only the weights in these projection matrices are updated during backpropagation.
*   This reduces the parameters trained from $8,000,000,000$ to about $40,000,000$ ($0.5\%$), enabling training on a single standard GPU.

### 3. Merging and Quantizing (GGUF f16 to Q4_K_M)
Once the adapter weights are saved:
1.  **Merge**: We load the base model and add the adapter weights directly to the base weights.
2.  **Convert to GGUF**: We run a script from `llama.cpp` to convert the Hugging Face weights format into a single GGUF binary file.
3.  **Quantize**: We round the 16-bit float weights into 4-bit integers. 

```
Float16 weight: 0.187384912803  (Uses 16 bits of storage)
Quantized 4-bit weight: 3        (Uses 4 bits of storage, mapped to a scale factor)
```
This reduces memory and CPU requirements by **75%**, enabling the model to run locally on your Mac's unified memory.
