# FutureEdge — Architectural Concepts & System Guide

This guide compiles and breaks down all the primary architectural, quantitative, and engineering concepts used across the **FutureEdge** multi-agent trading system. Use this as a reference to understand the design paradigms, code locations, difficulty ratings, and implementation details.

---

## 📊 Master Concept Map & Complexity Ratings

The table below lists each core concept, its relative difficulty, and its high-level domain.

| Concept | Domain | Difficulty | Primary Files |
| :--- | :--- | :---: | :--- |
| **LangGraph Workflows & State Machines** | Workflow Orchestration | 🔴 **High** (5/5) | [builder.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/builder.py), [state.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/state.py) |
| **Human-in-the-Loop (HITL) Checkpointing** | Concurrency & Safety | 🔴 **High** (5/5) | [human_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/human_agent.py), [runtime.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/runtime.py) |
| **Adaptive Weight Recalibration** | Learning Loops | 🟡 **Medium-High** (4/5) | [weight_updater.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/weight_updater.py) |
| **Episodic Vector Memory** | Memory Systems | 🟡 **Medium-High** (4/5) | [embedder.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/embedder.py), [qdrant_store.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/qdrant_store.py) |
| **Market Regime Classification** | Quantitative Math | 🟡 **Medium-High** (4/5) | [regime_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/regime_agent.py) |
| **Real-Time Event Streams (Redis Pub/Sub)** | Messaging / Networking | 🟡 **Medium** (3/5) | [redis.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/redis.py), [market_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/market_router.py) |
| **Kelly Criterion Sizing** | Quantitative Math | 🟡 **Medium** (3/5) | [risk_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/risk_agent.py) |
| **Multi-Agent Consensus Voting** | Design Patterns | 🟡 **Medium** (3/5) | [orchestration_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/orchestration_agent.py) |
| **Scoped DB Async Sessions & Repositories** | Data Persistence | 🟡 **Medium** (3/5) | [postgres.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/postgres.py), [trade_repo.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/repos/trade_repo.py) |
| **Continuous Background Jobs** | Concurrency | 🟡 **Medium** (3/5) | [exit_monitor.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/exit_monitor.py), [position_reconciler.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/position_reconciler.py) |
| **Broker Interface Abstraction** | Design Patterns | 🟢 **Easy** (2/5) | [base.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/brokers/base.py), [mock.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/brokers/mock.py) |
| **Feature Flag Configuration** | Dev Environment | 🟢 **Easy** (1/5) | [config.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/core/config.py) |

---

## 🛠️ Detailed Concept Breakdown

---

### 1. LangGraph Workflows & State Machines

#### What it is
The execution core of the multi-agent system, representing trading routines as a compiled directed graph. Nodes execute async logic, and edges control parallel fan-out (e.g., executing multiple technical/sentiment agents concurrently) and fan-in consolidation (orchestrator waiting for all agents to complete).

#### Complexity: 🔴 **High (5/5)**
Requires understanding graph compilation, async step-by-step state propagation, and append-only state updates (`Annotated` lists with `operator.add`).

#### Where it is used
- **Graph Compilation**: [builder.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/builder.py#L165) compiles the nodes and edges.
- **State Schema**: [state.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/state.py#L121) defines the `AgentState` TypedDict.
- **Workflow Run**: [runtime.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/runtime.py#L220) instantiates and handles compilation.

#### Code Snippet (State Merging & Concurrency)
```python
# From state.py — Append-only lists avoid race conditions in parallel branches
class AgentState(TypedDict):
    logs: Annotated[list[str], operator.add]
    completed_nodes: Annotated[list[str], operator.add]
```
```python
# From builder.py — Fan-out to four parallel agents, then fan-in to orchestrator
for agent in ["signal_agent", "sentiment_agent", "risk_agent", "portfolio_agent"]:
    builder.add_edge("regime_agent", agent)
    builder.add_edge(agent, "orchestrator")
```

#### Rationale & Insights
Using LangGraph prevents spaghetti async code. Raw `asyncio.gather` requires custom results merging, error propagation, and state logging. LangGraph provides typed state validation and automatic graph synchronization.

> [!WARNING]
> Do not overwrite state fields inside parallel nodes; return only the specific keys that agent is responsible for (e.g. `signal_vote`). Overwriting shared dictionary keys creates race conditions.

---

### 2. Human-in-the-Loop (HITL) Checkpointing

#### What it is
A safety gateway that halts order execution for high-risk trades. The graph saves its entire serialized state to PostgreSQL and exits. Once a Risk Manager issues an approval/rejection command, the graph loads the state back from the checkpointer database and continues execution from the exact line it paused on.

#### Complexity: 🔴 **High (5/5)**
Requires strict control of exception re-raising (`GraphInterrupt` must bypass try-catch blocks) and managing persistent PostgreSQL connection lifespans.

#### Where it is used
- **Pause & Interrupt**: [human_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/human_agent.py#L330) triggers `interrupt()` to stop execution.
- **DB Checkpointer**: [runtime.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/runtime.py#L217) initializes the `AsyncPostgresSaver`.
- **State Resume Router**: [workflow_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/workflow_router.py#L286) resumes execution.

#### Code Snippet (Interrupt Handling)
```python
# From human_agent.py — GraphInterrupt MUST be re-raised to bubble up to LangGraph
try:
    human_response = interrupt({"type": "human_review", "run_id": run_id})
except GraphInterrupt:
    raise
except Exception as e:
    return await _reject(state, reason=f"Interrupt failed: {e}")
```

#### Rationale & Insights
No HTTP request can stay open for hours waiting for a human manager. Storing the checkpoint in PostgreSQL allows the REST route to return a `200 OK` with status `PENDING` instantly. When the manager clicks "Approve", a different HTTP request invokes the resume command using the original `thread_id`.

---

### 3. Market Regime Classification

#### What it is
The practice of detecting the active market state (e.g. trending vs sideways) to shift indicators dynamically. Mean-reversion oscillators (RSI, Bollinger Bands) are heavily weighted in Rangebound regimes, while trend-following signals (MACD) dominate during Trending regimes.

#### Complexity: 🟡 **Medium-High (4/5)**
Requires calculation of quantitative formulas (Average True Range, Directional Movement Index, Average Directional Index) using rolling pandas dataframes.

#### Where it is used
- **Regime Agent Node**: [regime_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/regime_agent.py#L145) classifies the market state.
- **Adaptive Weight Shifting**: [signal_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/signal_agent.py#L178) changes scoring math based on `state["market_context"].regime`.

#### Mathematical Formulation
1. **True Range (TR)**:
   $$TR = \max \left( H_t - L_t, \, |H_t - C_{t-1}|, \, |L_t - C_{t-1}| \right)$$
2. **Average True Range (ATR)**:
   $$\text{ATR}_{14} = \text{Rolling Mean of } TR \text{ over 14 periods}$$
3. **Directional Movement (+DM, -DM)**:
   $$\text{If } (H_t - H_{t-1}) > (L_{t-1} - L_t) \text{ and } (H_t - H_{t-1}) > 0 \implies +DM = H_t - H_{t-1}, \text{ else } 0$$
   $$\text{If } (L_{t-1} - L_t) > (H_t - H_{t-1}) \text{ and } (L_{t-1} - L_t) > 0 \implies -DM = L_{t-1} - L_t, \text{ else } 0$$
4. **Directional Indicators (+DI, -DI)**:
   $$+DI_t = 100 \times \frac{\text{EMA}_{14}(+DM)_t}{\text{ATR}_{14, t}}, \quad -DI_t = 100 \times \frac{\text{EMA}_{14}(-DM)_t}{\text{ATR}_{14, t}}$$
5. **Directional Index (DX) & ADX**:
   $$\text{DX} = 100 \times \frac{|+DI - -DI|}{+DI + -DI}, \quad \text{ADX}_{14} = \text{EMA}_{14}(\text{DX})$$

#### Rationale & Insights
Oscillators are highly profitable in sideways markets but incur major losses during strong trends (due to false reversals). Shifting weights dynamically based on ADX ($>25$ indicates strong trend) prevents false contrarian entries.

---

### 4. Episodic Vector Memory

#### What it is
Storing market snapshots (numerical signals) and their final trade outcomes (win/loss percentage) as multidimensional float vectors. When evaluating a new setup, the system queries Qdrant for similar past situations to adapt execution confidence.

#### Complexity: 🟡 **Medium-High (4/5)**
Requires mapping heterogenous parameters (RSI, Volatility, Sentiment, Scores) to a normalized 12-dimensional vector and managing synchronous client database calls in a thread pool.

#### Where it is used
- **Vector Compilation**: [embedder.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/embedder.py#L464) creates the 12-dimension representation.
- **Qdrant Storage & Search**: [qdrant_store.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/memory/qdrant_store.py#L490) initiates searches and saves point data.
- **LLM Context Injection**: [llm_reasoner.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/models/llm_reasoner.py#L443) reads vector matches to generate text.

#### The 12-Dimensional Map
*   `dim 0`: RSI / 100 (Capped $[0, 1]$)
*   `dim 1`: $\tanh(\text{MACD Histogram} / 10)$ (Normalized $[-1, 1]$)
*   `dim 2`: Bollinger position ($(\text{Price} - \text{Lower}) / \text{Range}$)
*   `dim 3`: $\text{Volatility} / 0.20$ (Normalized $[0, 1]$)
*   `dim 4`: Sentiment Score (Scale $[-1, 1]$)
*   `dim 5–8`: One-hot encoded Regime flags (`TRENDING_UP`, `TRENDING_DOWN`, `RANGEBOUND`, `HIGH_VOLATILITY`)
*   `dim 9–11`: Normal consensus scores (`buy_score`, `sell_score`, `risk_score`)

#### Rationale & Insights
Language model embeddings are meant for semantic text, not raw quantitative data. A custom 12-dimensional vector preserves mathematical ratios so Cosine Similarity measures actual market condition proximity.

---

### 5. Adaptive Agent Weight Shifting

#### What it is
A statistical feedback loop that monitors agent predictions. If an agent is historically correct on a ticker, its voting weight is increased; if incorrect, its weight is reduced.

#### Complexity: 🟡 **Medium-High (4/5)**
Requires querying trade history JSONB data, aligning voter decisions (BUY/SELL) against real outcome directions (LONG/SHORT) and P&L results, and applying safety clipping bounds.

#### Where it is used
- **Feedback Job**: [weight_updater.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/weight_updater.py#L414) runs calculations after every 20 closed trades.
- **Redis Cache & Retrieve**: [redis.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/redis.py#L332) registers cache reads/writes.
- **Scoring Weight Consumption**: [orchestration_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/orchestration_agent.py#L255) retrieves current weights.

#### Sizing Weights Constraints
Weights are normalized to sum to $1.0$, but clipped individually to prevent complete system collapse:
$$\text{Weight Range: } [0.10, 0.50]$$
- **Lower bound ($0.10$)**: Ensures a currently "unlucky" agent is never completely silenced ($0.0$), maintaining voting diversity.
- **Upper bound ($0.50$)**: Prevents a temporarily "lucky" agent from dominating the entire consensus.

---

### 6. Kelly Criterion Position Sizing

#### What it is
An asset allocation model that calculates optimal exposure sizes based on historical win probability and average reward-to-risk ratio.

#### Complexity: 🟡 **Medium (3/5)**
Requires calculating trade statistics from database logs and scaling down sizing for real-world risk boundaries.

#### Where it is used
- **Historical Analysis**: [trade_repo.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/repos/trade_repo.py#L436) parses the database for user stats.
- **Optimal Sizing Node**: [risk_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/risk_agent.py#L119) processes the size fraction.

#### Formula & Constraints
$$f^* = \frac{p \cdot b - (1 - p)}{b}$$
Where:
- $p$: Win Rate (Realized P&L $> 0$)
- $b$: Reward-to-Risk ratio ($\text{Average Profit of Wins} / \text{Average Loss of Losses}$)

The calculated Kelly fraction is:
1. **Halved**: Half-Kelly sizing ($f^* / 2$) is utilized to cut drawdown volatility by $50\%$ while preserving $75\%$ of growth.
2. **Clamped**: The final capital allocation is clamped to $[2\%, 25\%]$ of total equity.
3. **Minimum History**: Requires a minimum of **10 closed trades** for the symbol; otherwise, it falls back to a default allocation ($2\%$) to prevent fitting curves to noise.

---

### 7. Multi-Agent Consensus Voting

#### What it is
An isolation pattern where individual agents independently issue a `BUY`, `SELL`, `HOLD`, or `VETO` decision based on their narrow domains. The orchestrator computes a weighted consensus to decide the final execution direction.

#### Complexity: 🟡 **Medium (3/5)**
Requires aggregating isolated votes and managing hard veto bounds.

#### Where it is used
- **Consensus Orchestration**: [orchestration_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/orchestration_agent.py#L247) runs the main scoring logic.
- **Decision Nodes**: Implemented across agents in the `backend/app/agents/` directory.

#### VETO Logic
If any safety agent (e.g. Risk Agent due to high margin usage or late-day time limits) issues a `VETO` vote:
$$\text{Consensus Direction} \implies \text{NONE (Halted)}$$
The trade is aborted immediately, overriding any positive consensus buy scores.

---

### 8. Scoped Database Async Session & Repository Pattern

#### What it is
Decoupling raw SQL / SQLAlchemy query operations from HTTP routes and business nodes into dedicated repositories. Scoped async sessions are managed using context managers to prevent connection leaks.

#### Complexity: 🟡 **Medium (3/5)**
Requires configuring thread-safe async generators, connection pool scaling limits, and disabling lazy-load commits.

#### Where it is used
- **Connection Factory**: [postgres.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/postgres.py#L213) builds the async engine.
- **Data Repositories**: [trade_repo.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/repos/trade_repo.py#L415) and [user_repo.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/repos/user_repo.py#L108).

#### Core Configuration Rule
```python
AsyncSessionLocal = async_sessionmaker(..., expire_on_commit=False)
```
Disabling `expire_on_commit` prevents SQLAlchemy from clearing loaded attributes on ORM objects after committing. If enabled, accessing fields (e.g., `trade.id`) outside the active database transaction context raises a `DetachedInstanceError` in async execution.

---

### 9. Continuous Background Monitoring Jobs

#### What it is
Asynchronous coroutine loops running alongside the main FastAPI application server. They monitor open trades for Stop-Loss (SL) / Take-Profit (TP) boundaries, verify broker balances, and reconcile discrepancies.

#### Complexity: 🟡 **Medium (3/5)**
Requires managing async task lifespans to prevent orphaned tasks, and wrapping the loops in try-except blocks so individual errors do not crash the daemon process.

#### Where it is used
- **Lifespan Startup & Shutdown**: Managed inside the `lifespan` hook in [runtime.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/graph/runtime.py#L238).
- **Daemon Loops**: [exit_monitor.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/exit_monitor.py#L515) and [position_reconciler.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/position_reconciler.py#L408).

#### Structure Pattern (Safe Task Management)
```python
class BackgroundJob:
    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
```

---

### 10. Real-Time Event Streams (Redis Pub/Sub & Streams)

#### What it is
A messaging subsystem that routes rapid updates (e.g., market tick changes, agent logs, execution results) to the frontend via WebSockets. It acts as a memory cache for high-frequency flags.

#### Complexity: 🟡 **Medium (3/5)**
Requires understanding thread-safe Redis clients, stream message appending boundaries, and pub/sub subscriptions.

#### Where it is used
- **Client Configuration**: [redis.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/db/redis.py#L215).
- **Subscribing WebSocket Bridge**: [market_router.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/api/routes/market_router.py#L430).
- **Publishing Events**: Executed in [orchestration_agent.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/agents/orchestration_agent.py#L298) and [exit_monitor.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/jobs/exit_monitor.py#L393).

#### Stream Buffering
For high-frequency tick data, `xadd` is configured with `maxlen=1000` to buffer tick history while discarding older updates, avoiding memory expansion.

---

### 11. Broker Interface Abstraction

#### What it is
Loose coupling of execution logic from broker-specific endpoints. By utilizing a common abstract class, the system switches between paper trading (Mock Broker) and live execution (Zerodha Kite Connect) with zero modifications to the core agents.

#### Complexity: 🟢 **Easy (2/5)**
Standard Object-Oriented Programming (OOP) subclassing and factory initialization.

#### Where it is used
- **Base Interface**: [base.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/brokers/base.py#L213).
- **Broker Factory**: [mock.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/brokers/mock.py#L225) and [zerodha.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/brokers/zerodha.py#L149).

---

### 12. Feature Flag Configuration

#### What it is
Centralizing application toggles (e.g. enabling FinBERT sentiment analysis, allowing LLM rationalization, choosing the active broker mode) in typed Pydantic models. It allows switching behaviors via `.env` adjustments without refactoring code.

#### Complexity: 🟢 **Easy (1/5)**
Basic configuration parameters.

#### Where it is used
- **Settings Initialization**: [config.py](file:///Users/baijuyadav/Desktop/futureedge/backend/app/core/config.py#L55).
