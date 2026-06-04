# FutureEdge — Senior Engineer Interview Coding Challenges

This document contains four hands-on, senior-level coding challenges designed to help you build muscle memory and master the critical concepts used in FutureEdge. Each challenge is framed as a senior engineer interview question, followed by a step-by-step assignment and a clean Python code template.

You can write your implementations in the [scratch directory](file:///Users/baijuyadav/.gemini/antigravity-ide/scratch/) and execute them to verify your logic.

---

## 📚 Challenges Index

1. [Challenge 1: The Pausable State Machine (Simulated LangGraph + HITL)](#challenge-1-the-pausable-state-machine-simulated-langgraph--hitl)
2. [Challenge 2: The Custom Numerical Embedder & Similarity Index](#challenge-2-the-custom-numerical-embedder--similarity-index)
3. [Challenge 3: Online Accuracy-Based Agent Weight Recalibrator](#challenge-3-online-accuracy-based-agent-weight-recalibrator)
4. [Challenge 4: The Scoped Async Daemon Worker Loop](#challenge-4-the-scoped-async-daemon-worker-loop)

---

## Challenge 1: The Pausable State Machine (Simulated LangGraph + HITL)

### 💬 Senior Interview Question
> *"In a distributed system, how would you design a pausable workflow engine that can run multiple tasks concurrently, aggregate their results, and pause for an asynchronous, human-in-the-loop (HITL) approval step that might take hours or days to complete, without blocking thread execution or holding in-memory references?"*

### 💡 Core Concepts
- State serialization and preservation across process boundaries.
- Concurrent task orchestration (`asyncio.gather`).
- Checkpointer DB integrations (saving/re-hydrating state from SQLite/JSON).
- Non-blocking state resumes via thread IDs.

### 📝 Step-by-Step Assignment
1. Define a `WorkflowState` dictionary with fields: `thread_id`, `symbol`, `votes`, `status`, and `decision`.
2. Implement three async node functions:
   - `run_agents(state)`: Runs two mock agents concurrently using `asyncio.gather` and appends their votes to `state["votes"]`.
   - `orchestrator(state)`: Evaluates if agent votes disagree. If they disagree, set `state["status"] = "PENDING_REVIEW"` and raise a `WorkflowInterrupt` exception.
   - `execute_trade(state)`: Prints a mock order placement message.
3. Build a mock checkpointer class that saves and loads JSON-serialized states by a unique `thread_id`.
4. Create a `WorkflowEngine` class that executes the nodes sequentially, handles early interrupts by saving state, and resumes a checkpoint using `resume(thread_id, approve)`.

### 🏗️ Code Blueprint Template
Create a file at `/Users/baijuyadav/.gemini/antigravity-ide/scratch/challenge1.py` and implement the code:

```python
import asyncio
import json
import uuid
from typing import TypedDict, Dict, Any, List

class WorkflowState(TypedDict):
    thread_id: str
    symbol: str
    votes: Dict[str, str]
    status: str      # "RUNNING", "PENDING_REVIEW", "COMPLETED", "REJECTED"
    decision: str    # "BUY", "SELL", "HOLD"

class WorkflowInterrupt(Exception):
    """Raised when the workflow needs human review."""
    pass

class MockCheckpointer:
    """Simulates persistent storage (e.g., PostgreSQL)."""
    def __init__(self):
        self.db: Dict[str, str] = {}

    def save(self, thread_id: str, state: WorkflowState):
        self.db[thread_id] = json.dumps(state)

    def load(self, thread_id: str) -> WorkflowState:
        if thread_id not in self.db:
            raise ValueError("Thread state not found")
        return json.loads(self.db[thread_id])

async def run_agents(state: WorkflowState) -> WorkflowState:
    print("🤖 Running Signal and Sentiment Agents in parallel...")
    # Simulate concurrent work
    await asyncio.sleep(0.5)
    # Write mock votes into state
    state["votes"] = {"SignalAgent": "BUY", "SentimentAgent": "SELL"}
    return state

async def orchestrator(state: WorkflowState) -> WorkflowState:
    print("🧠 Orchestrator evaluating votes...")
    votes = list(state["votes"].values())
    
    # If agents disagree, raise WorkflowInterrupt to trigger HITL
    if len(set(votes)) > 1:
        state["status"] = "PENDING_REVIEW"
        print("⚠️ Agents disagreed. Pausing workflow for HITL approval!")
        raise WorkflowInterrupt()
        
    state["status"] = "COMPLETED"
    state["decision"] = votes[0]
    return state

async def execute_trade(state: WorkflowState) -> WorkflowState:
    print(f"🚀 Executing trade for {state['symbol']}! Decision: {state['decision']}")
    state["status"] = "COMPLETED"
    return state

class WorkflowEngine:
    def __init__(self, checkpointer: MockCheckpointer):
        self.checkpointer = checkpointer

    async def run(self, thread_id: str, symbol: str) -> WorkflowState:
        # TODO: Initialize state, call run_agents and orchestrator.
        # Catch WorkflowInterrupt, save state to checkpointer, and return.
        pass

    async def resume(self, thread_id: str, approve: bool) -> WorkflowState:
        # TODO: Load state, update status/decision based on approve flag, and execute.
        pass

# --- Verification Driver Script ---
async def main():
    checkpointer = MockCheckpointer()
    engine = WorkflowEngine(checkpointer)
    thread_id = str(uuid.uuid4())[:8]
    
    print("--- FIRST RUN (Should Pause on Disagreement) ---")
    state = await engine.run(thread_id, "NIFTY 50")
    print(f"Returned Status: {state['status']}\n")
    
    print("--- RESUME WORKFLOW (Risk Manager Approves) ---")
    final_state = await engine.resume(thread_id, approve=True)
    print(f"Final Status: {final_state['status']}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Challenge 2: The Custom Numerical Embedder & Similarity Index

### 💬 Senior Interview Question
> *"Semantic vector models (like OpenAI or BERT text embeddings) are standard for text, but they fail on pure numerical datasets like high-frequency technical signals. How would you design a custom, low-latency, numerical vector space that normalizes inputs and performs cosine-similarity matching to find historical scenarios?"*

### 💡 Core Concepts
- Quantitative signal normalization.
- Mapping variables to a fixed-dimensional floating vector.
- Cosine similarity calculation.
- Cosine distance matching with metadata filters.

### 📝 Step-by-Step Assignment
1. Write a function `normalize_snapshot` that scales diverse parameters to a $[-1.0, 1.0]$ range:
   - RSI (0 to 100) scaled to $[-1.0, 1.0]$.
   - Volatility (0.0 to 0.10) scaled to $[0.0, 1.0]$.
   - Trend (Bullish, Bearish, Sideways) represented as $1.0$, $-1.0$, or $0.0$.
   - Sentiment score represented directly as $[-1.0, 1.0]$.
2. Implement `calculate_cosine_similarity` using pure Python math to calculate the distance between two vectors.
3. Build a `SimpleVectorDB` containing list dictionaries:
   - `upsert(vector, payload)`: Appends data.
   - `search(query_vector, symbol, limit)`: Computes similarity scores, filters by symbol, sorts descending, and returns the top $K$ points.

### 🏗️ Code Blueprint Template
Create a file at `/Users/baijuyadav/.gemini/antigravity-ide/scratch/challenge2.py` and implement the code:

```python
import math
from typing import List, Dict, Any

def normalize_snapshot(rsi: float, vol: float, trend: str, sentiment: float) -> List[float]:
    # TODO: Implement normalization
    # - Scale rsi: (rsi - 50) / 50
    # - Scale vol: min(vol / 0.10, 1.0)
    # - Map trend: 'bullish' -> 1.0, 'bearish' -> -1.0, others -> 0.0
    # - Map sentiment directly
    return []

def calculate_cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    # TODO: Implement cosine similarity calculation
    # formula: dot_product(A, B) / (magnitude(A) * magnitude(B))
    return 0.0

class SimpleVectorDB:
    def __init__(self):
        self.points: List[Dict[str, Any]] = []

    def upsert(self, vector: List[float], payload: Dict[str, Any]):
        self.points.append({"vector": vector, "payload": payload})

    def search(self, query_vector: List[float], symbol: str, limit: int = 3) -> List[Dict[str, Any]]:
        # TODO: Calculate similarities, filter by symbol, sort descending, and return limit.
        return []

# --- Verification Driver Script ---
if __name__ == "__main__":
    db = SimpleVectorDB()
    
    # Seeding database
    v1 = normalize_snapshot(rsi=28.0, vol=0.015, trend="bearish", sentiment=-0.4)
    db.upsert(v1, {"symbol": "NIFTY 50", "outcome": "WIN", "pnl": 1.2})
    
    v2 = normalize_snapshot(rsi=72.0, vol=0.022, trend="bullish", sentiment=0.5)
    db.upsert(v2, {"symbol": "NIFTY 50", "outcome": "LOSS", "pnl": -0.8})

    v3 = normalize_snapshot(rsi=32.0, vol=0.018, trend="bearish", sentiment=-0.3)
    db.upsert(v3, {"symbol": "NIFTY 50", "outcome": "WIN", "pnl": 0.5})
    
    # Query with a new oversold setup
    query = normalize_snapshot(rsi=29.0, vol=0.016, trend="bearish", sentiment=-0.35)
    results = db.search(query, symbol="NIFTY 50", limit=2)
    
    print("--- Top Similar Trade Scenarios ---")
    for r in results:
        print(f"Similarity: {r['similarity']:.4f} | Outcome: {r['payload']['outcome']} | PnL: {r['payload']['pnl']}%")
```

---

## Challenge 3: Online Accuracy-Based Agent Weight Recalibrator

### 💬 Senior Interview Question
> *"In a multi-agent consensus system, we have multiple predictive agents contributing to decisions. Over time, some agents will degrade in accuracy, while others improve. How do you design a thread-safe, online recalculation loop that adjusts agent influence weights based on their historical accuracy, while preventing weight monopolies (single agent dominating) and deadlocks (silencing an agent entirely)?"*

### 💡 Core Concepts
- Online performance validation (aligning active decisions to binary win/loss targets).
- Dynamic weight distribution.
- Clip bounds (enforcing min/max limits).
- Re-normalization of vectors to sum to $1.0$.

### 📝 Step-by-Step Assignment
1. Create a `MockClosedTrade` container to simulate database trade records.
2. Build `update_agent_weights(trades)`:
   - Identify active votes (`BUY`/`SELL`). Exclude `HOLD` votes.
   - Increment correct counters if votes align with winning outcomes (e.g. `BUY` on `LONG`/`WIN`).
   - Calculate accuracy per agent.
   - Apply clipping boundaries: clip agent weights between $[0.10, 0.50]$.
   - Normalize the clipped weights so they sum exactly to $1.0$.

### 🏗️ Code Blueprint Template
Create a file at `/Users/baijuyadav/.gemini/antigravity-ide/scratch/challenge3.py` and implement the code:

```python
from typing import List, Dict, Any

class MockClosedTrade:
    def __init__(self, direction: str, outcome: str, agent_votes: Dict[str, str]):
        self.direction = direction    # "LONG" or "SHORT"
        self.outcome = outcome        # "WIN" or "LOSS"
        self.agent_votes = agent_votes # e.g. {"SignalAgent": "BUY", "SentimentAgent": "SELL"}

def update_agent_weights(trades: List[MockClosedTrade]) -> Dict[str, float]:
    agents = ["SignalAgent", "SentimentAgent", "RiskAgent", "PortfolioAgent"]
    correct_counts = {a: 0 for a in agents}
    total_votes = {a: 0 for a in agents}

    # TODO: Calculate correct votes and total active votes per agent.
    # Clip accuracies between 0.10 and 0.50.
    # Normalize weights so they sum to 1.0.
    return {}

# --- Verification Driver Script ---
if __name__ == "__main__":
    mock_history = [
        # SignalAgent voted BUY on a LONG/WIN (Correct)
        MockClosedTrade("LONG", "WIN", {"SignalAgent": "BUY", "SentimentAgent": "SELL", "RiskAgent": "BUY"}),
        # SignalAgent voted BUY on a LONG/LOSS (Incorrect)
        MockClosedTrade("LONG", "LOSS", {"SignalAgent": "BUY", "SentimentAgent": "SELL", "RiskAgent": "HOLD"}),
        # SignalAgent voted BUY on a SHORT/LOSS (Correct - contrarian perspective)
        MockClosedTrade("SHORT", "LOSS", {"SignalAgent": "BUY", "SentimentAgent": "BUY", "RiskAgent": "HOLD"}),
    ]
    
    new_weights = update_agent_weights(mock_history)
    print("--- Updated Consensus Agent Weights ---")
    for agent, wt in new_weights.items():
        print(f"{agent}: {wt:.4f}")
    print(f"Total Weight Sum: {sum(new_weights.values()):.2f}")
```

---

## Challenge 4: The Scoped Async Daemon Worker Loop

### 💬 Senior Interview Question
> *"When building scalable Web APIs (like FastAPI), we frequently need background workers to perform tasks like exit monitoring (SL/TP triggers) or cache reconciliation. How do you design an asynchronous background daemon worker that executes a periodic loop, uses a scoped resource context manager to prevent database connection leaks, and implements graceful shutdown handling?"*

### 💡 Core Concepts
- Graceful async task lifespans (`asyncio.create_task` and cancellations).
- Context managers for resource containment.
- Error mitigation inside daemons (preventing runtime crashes).
- Thread-safe shutdown routines.

### 📝 Step-by-Step Assignment
1. Create a `MockDBSession` async context manager that prints connection acquisition and release operations.
2. Implement the `DaemonWorker` start/stop hooks:
   - `start()`: Launches the infinite async run loop without blocking execution.
   - `stop()`: Signals the worker, cancels the async task, and awaits termination.
3. Write the runner loop:
   - Evaluates mock data inside a `MockDBSession` context block.
   - Sleeps for the target interval using `asyncio.sleep` non-blockingly.
   - Catches transient exceptions so the daemon does not crash.

### 🏗️ Code Blueprint Template
Create a file at `/Users/baijuyadav/.gemini/antigravity-ide/scratch/challenge4.py` and implement the code:

```python
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Worker")

class MockDBSession:
    async def __aenter__(self):
        logger.info("🔌 Database session created & connection pulled.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info("🔒 Database session committed & connection released.")
        return False

    async def query_open_trades(self) -> list:
        return [{"id": 101, "symbol": "NIFTY 50", "stop_loss": 22000.0}]

class DaemonWorker:
    def __init__(self, interval: int = 1):
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("🟢 Daemon Worker started in background.")

    async def stop(self):
        # TODO: Stop running, cancel self._task, handle CancelledError, and reset.
        pass

    async def _loop(self):
        # TODO: Implement loop, allocate MockDBSession context, run mock evaluations, and sleep.
        pass

# --- Verification Driver Script ---
async def main():
    worker = DaemonWorker(interval=1)
    await worker.start()
    
    # Let the background daemon run for 3 seconds
    await asyncio.sleep(3.2)
    
    logger.info("🛑 Shutting down server...")
    await worker.stop()
    logger.info("🏁 Server shutdown complete.")

if __name__ == "__main__":
    asyncio.run(main())
```
