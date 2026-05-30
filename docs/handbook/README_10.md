# README 10 — ENGINEER'S THINKING: WHY EVERYTHING WAS BUILT THIS WAY

## On System Design

### Why LangGraph instead of raw asyncio?

**What you gain:**
1. Checkpointing is free — `interrupt()` + PostgreSQL saves the entire workflow state automatically. Raw asyncio would require custom serialisation, checkpoint tables, a resume API, and error handling for every edge case.
2. The fan-out/fan-in is declarative — adding a new agent means one `add_edge()` line. In asyncio you'd need `asyncio.gather()` + result merging + partial failure handling.
3. The state contract is enforced — `AgentState` as a TypedDict means every node's output is typed. In raw asyncio, you'd pass dicts around and typos would fail silently at runtime.

**The trade-off:**
LangGraph adds a dependency and a learning curve. If HITL was not a requirement, raw asyncio with `asyncio.gather()` would have been simpler.

**The design principle:**
Use a framework when the framework's primitive operations match your domain primitives. LangGraph's primitives are nodes, edges, and checkpoints — and this system's primitives are agents, dependencies, and HITL pauses. Perfect alignment.

---

### Why multi-agent consensus instead of one model?

**The problem with a single model:**
- A single neural network trained on historical data overfits to the patterns it was trained on
- Markets are non-stationary — a TRENDING pattern in 2022 may not predict 2026 the same way
- No single indicator is reliable in all market regimes

**The consensus solution:**
Each agent specialises in a narrow signal:
- `signal_agent` = technical indicators (price patterns)
- `sentiment_agent` = news/NLP (external information)
- `risk_agent` = portfolio constraints (capital protection)
- `portfolio_agent` = current exposure (position management)

The orchestrator treats them as independent evidence sources. When they agree, confidence is high. When they disagree, the system is conservative.

**The regime adaptation insight:**
In a RANGEBOUND market, RSI and Bollinger Bands predict mean-reversion well. In a TRENDING market, MACD predicts continuation well. Using the same weights for all market conditions is statistically wrong. `regime_agent` runs first precisely so all downstream agents can adapt.

---

### Why the Kelly Criterion for position sizing?

**What Kelly solves:**
Fixed position sizes (e.g. always trade 2% of capital) don't adapt to the system's actual edge. Kelly calculates the mathematically optimal bet size given your win rate and win/loss ratio.

**The half-Kelly choice:**
Full Kelly is the theoretically optimal fraction, but it's extremely volatile in practice — a losing streak causes massive drawdowns. Half-Kelly (Kelly/2) is a standard risk management choice. It gives roughly 75% of Kelly's geometric growth rate while halving volatility. This is the industry convention, not an arbitrary choice.

**The minimum 10-trade requirement:**
Kelly with 5 trades of data is statistically meaningless — you'd be fitting a curve to noise. The 10-trade minimum uses conservative defaults until there's enough history. New users effectively trade minimum size until they've built a track record.

**What this means for the system:**
A user with a 70% win rate and 2:1 reward/risk ratio gets a Kelly fraction of ~0.20 (20% of capital). Halved: 10%. On ₹100,000 equity: ₹10,000 per trade. This is how Kelly prevents over-betting and under-betting simultaneously.

---

### Why three separate databases?

**PostgreSQL for persistent relational data:**
Trades, users, workflow runs, auth tokens all have relationships and need ACID transactions. A trade must be committed atomically with its audit trail. An interrupted write is unacceptable — you can't have a trade exist in the broker but not in the DB.

**Redis for ephemeral fast data:**
The kill switch must be checked on every trade attempt. If it hits PostgreSQL every 10ms per active trade, that's 86,400 queries per user per day just for the safety check. Redis reads are microsecond operations. Similarly, indicator cache (60s TTL) and tick streams need high-frequency reads/writes that PostgreSQL can't match.

**Qdrant for vector similarity:**
PostgreSQL can't efficiently find the 5 most similar 12-dimensional vectors out of 10,000 stored points. It would require a full table scan with cosine similarity computation on every row. Qdrant uses HNSW (Hierarchical Navigable Small World) graphs — it finds similar vectors in O(log n) time. The right tool for the right query type.

---

### Why the exit monitor is a separate background job instead of in the workflow?

**The constraint:**
A trade can be open for hours or days. The exit check must run continuously, not just when a user triggers a workflow cycle.

**What breaks if it's in the workflow:**
If exit monitoring happened inside `execution_agent`, exits would only be checked when a user runs a new cycle. In overnight positions during a crash, the trade would stay open for hours past the stop-loss.

**What breaks if it's in the HTTP request:**
No HTTP request runs continuously for 8 hours.

**The asyncio background task:**
`asyncio.create_task(_monitor_loop())` creates a coroutine that runs alongside the main event loop, waking up every 10 seconds, completely independent of HTTP requests. It runs on the same event loop, so it can use `await` without any threading complications.

**Why the class with start/stop:**
Bare `asyncio.create_task()` at module import time would start before the database and Redis are ready. The `ExitMonitor.start()` call in `lifespan()` ensures everything is connected first. The `stop()` call in lifespan shutdown ensures clean termination.

---

### Why the LLM explains but doesn't decide?

**The alignment argument:**
If GPT makes the trading decision, you have a black box at the core of a financial system. You can't audit why it made a decision. You can't test it deterministically. Its outputs vary between API calls. It can be unavailable.

**The engineering argument:**
The mathematical consensus (weighted vote + Kelly + ATR-based SL/TP) is deterministic, testable, and auditable. You can write unit tests that verify it always produces LONG when SignalAgent votes BUY with high confidence and risk is low.

**The practical argument:**
LLM API calls can fail. If the decision depended on GPT, every API outage = system down. The graceful fallback to `_template_rationale()` means the trading system is completely independent of LLM availability.

**The regulatory argument:**
Financial systems must be explainable. "GPT decided to trade" is not a valid explanation. "3 of 4 agents voted BUY, RSI was 27.3 (oversold), buy score exceeded 0.55 threshold" is explainable and auditable.

---

### Why custom 12-dimensional embedder instead of a language model?

**The category error:**
Language model embeddings (BERT, GPT, sentence-transformers) encode semantic meaning of text. They understand "RSI is oversold" means something similar to "momentum indicator shows buying opportunity" — because they've seen these phrases in text.

But our vector is [0.273, 0.012, 0.3, 0.09, 0.35, 0.0, 0.0, 0.0, 0.0, 0.68, 0.12, 0.42]. These are numbers, not words. An LM embedding of this list of floats would encode their string representation, not their numerical relationships.

**The numerical similarity insight:**
We want RSI=28 to be "similar" to RSI=31, and RSI=75 to be "similar" to RSI=78, but RSI=28 should be "different" from RSI=75. The custom normalisation (RSI/100 → [0,1]) preserves numerical relationships. Cosine similarity on these normalised vectors gives meaningful similarity scores.

**The dimension design:**
Each dimension captures an independent market signal:
- RSI/100 → overbought/oversold
- tanh(MACD/10) → momentum direction
- Bollinger position → mean-reversion potential
- Volatility/0.20 → market stability
- Sentiment → external narrative
- Regime (one-hot) → market structure
- Buy/sell/risk scores → system conviction

12 dimensions was chosen as the minimum to represent these independent signals without redundancy.

---

### Why adaptive agent weights?

**The problem with static weights:**
If you set SignalAgent weight=0.30 at launch, it stays 0.30 forever even if:
- SignalAgent is 80% accurate on RELIANCE but only 40% accurate on NIFTY 50
- SentimentAgent becomes much more accurate after you switch to FinBERT
- A new market regime (post-COVID options) makes technical signals unreliable

**The adaptive solution:**
After every 20 closed trades, recalculate each agent's accuracy from their historical votes vs trade outcomes. Weight proportional to accuracy. An agent that's been right 70% of the time gets more influence than one right 50% of the time.

**The feedback loop:**
Execution agent stores agent votes in `agent_consensus` JSONB column. Exit monitor closes trades and records PnL. Weight updater reads votes + PnL, calculates accuracy, updates Redis. Orchestrator reads weights from Redis on next cycle. This loop takes N trades to complete (N = WEIGHT_UPDATE_INTERVAL_TRADES).

**Why min/max bounds (0.10 to 0.50):**
Without bounds, a temporarily lucky agent could capture 90% of the weight, making the system a single-agent system. Without a minimum, a temporarily unlucky agent gets zeroed out and the system loses one of its signal sources permanently. The 0.10 minimum preserves diversity; the 0.50 maximum prevents domination.

---

### Why two modes for sentiment (keywords vs FinBERT)?

**The demo problem:**
FinBERT requires downloading a 440MB PyTorch model. In a demo environment, CI/CD, or development, you don't want to download 440MB just to test the sentiment node. The keyword fallback provides functional sentiment analysis with zero dependencies.

**The production path:**
FinBERT (ProsusAI/finbert) is fine-tuned on financial news text. It understands financial jargon, context, and sentiment nuances. "Q2 guidance below consensus" → negative, even without the word "negative" appearing. Keywords would miss this.

**The toggle pattern:**
`FINBERT_ENABLED=false` in `.env` → keyword analysis. `FINBERT_ENABLED=true` → download and use FinBERT. The code path is `if settings.FINBERT_ENABLED:` — zero overhead when disabled.

---

### Why the repository pattern over direct ORM in routes?

**The argument from change:**
If you query `Trade` directly in 8 different routes and need to add a `deleted_at` soft-delete column, you'd need to update 8 `where` clauses. With `TradeRepo`, you update 1 `get_user_trades()` method.

**The argument from security:**
The `user_id` filter must appear in every trade query. If it's in `TradeRepo.get_user_trades()`, it's enforced in one place. If every route writes its own SQL, a developer can forget the `user_id` filter and create a data leak.

**The argument from testing:**
```python
# Testing a route without database
async def fake_get_trades(session, user_id, ...): return [mock_trade]
app.dependency_overrides[get_db] = lambda: AsyncMock()
# Override TradeRepo in the route → test business logic without DB

# Testing repo logic without HTTP
async with AsyncSessionLocal() as session:
    trades = await TradeRepo.get_user_trades(session, user_id="test-user")
    assert len(trades) == 3
```

---

### Why the broker abstraction (BrokerBase)?

**The test argument:**
`MockBroker.place_order()` always returns `OrderResult(success=True, fill_price=price*1.0005)`. This lets you run the entire workflow — regime detection, signal analysis, risk calculation, consensus, execution — without connecting to any external API. Tests are deterministic.

**The swap argument:**
When you add IIFL or Angel Broking support, you implement `IIFLBroker(BrokerBase)`. The execution_agent never changes. The `get_broker()` factory function adds one branch. No other code touches.

**The failure isolation argument:**
If Zerodha's API changes an endpoint, only `zerodha.py` changes. The rest of the system is unchanged and unaffected.

---

### Why the kill switch uses Redis instead of PostgreSQL?

**The latency argument:**
Kill switch check happens in `execution_node` on every trade attempt. PostgreSQL SELECT with network round-trip: ~2-5ms. Redis GET with decode: ~0.1ms. At high frequency, this matters.

**The availability argument:**
If PostgreSQL is temporarily unreachable (slow query, lock contention, replica lag), a Redis-backed kill switch still fires. A PostgreSQL-backed kill switch could fail precisely when you need it most — during a system stress event that also overloads the database.

**The simplicity argument:**
A kill switch is a boolean. Redis `SET key 1` / `GET key` / `DEL key` is the simplest possible implementation for a boolean that needs fast reads.

---

### Why the LIMIT order for exits (not MARKET)?

**The SEBI compliance argument:**
Some SEBI regulations require limit orders for algorithmic trading. A market order has no price bound — in a flash crash, you could exit at any price.

**The slippage control argument:**
```python
price_buffer = exit_price * 0.0005   # 0.05%
limit_price  = exit_price - price_buffer  # for LONG exit (SHORT order)
```
The 0.05% buffer ensures the order fills quickly (it's just below last price) while preventing extreme slippage.

**The worst case argument:**
If the limit order doesn't fill (price moves away), the exit monitor will try again on the next 10-second cycle. The trade stays open but the system keeps trying. A market order would fill instantly but potentially at a terrible price.

---

### On the overall architecture evolution (Phase 1 → Phase 2)

**Phase 1 design** (what was built first):
- Fixed agent weights
- No episodic memory
- No adaptive Kelly (fixed 2% position)
- No exit monitor (trades never closed automatically)
- Keyword-only sentiment

**Phase 2 additions** (layer on top of Phase 1):
- Adaptive weights (weight_updater.py)
- Episodic memory (Qdrant, embedder.py, qdrant_store.py)
- Kelly from real trade history (TradeRepo.calculate_win_stats)
- Exit monitor (exit_monitor.py)
- FinBERT toggle (sentiment_agent.py)

**The engineering insight:**
Build the simplest version that works first. Validate the core loop (market data → agents → consensus → order) before adding learning and adaptation. The Phase 2 additions are only useful if Phase 1 produces real trade history. This is why the sequence matters — you can't have learning without data, and you can't have data without a working Phase 1.

**The pattern for your next project:**
1. Build the happy path with hardcoded parameters
2. Verify the system produces correct outputs
3. Add learning loops that adapt those parameters
4. Add monitoring and safety systems
5. Add real data sources to replace mocks

This is bottom-up system construction. The opposite — designing a complete adaptive system before validating the core — is how complex AI systems fail to ship.
