# FutureEdge: Advanced Systems Engineering Learnings & Assignment

This document contains a high-level summary of what we solved today, followed by an **advanced, production-grade systems engineering assignment** designed to elevate your backend, concurrent programming, and mathematical trading skills, complete with implementation steps and resources.

---

## 🧠 Part 1: Today's Architectural Lessons

Today we focused on **systems integration, data synchronicity, and edge-case prevention** in low-latency environments:

1. **Deterministic Simulations vs. Live Markets**: 
   A mock broker must faithfully replicate the constraints of a live exchange (such as dynamic prices, spreads, and execution times). Hardcoding mock values breaks downstream systems (like our automatic exit monitor).
2. **State Reconciliation & Soft Execution**: 
   How to safely transition database states (`OPEN` -> `CLOSED`) while executing concurrent API requests, ensuring database transactions are isolated, fast, and publish clean events to pub/sub (Redis).
3. **UI Optimistic Updates**:
   Using client-side mutation strategies (`mutate` in SWR) to immediately reflect changes on the interface (like manual exits) while waiting for backend confirmation.

---

## 💻 Part 2: The Advanced Systems Assignment

This assignment challenges you to build a **Low-Latency Order Book & Reactive Risk Engine**. This is a classic systems project that covers memory safety, fast data structures, concurrency, and event-driven architectures.

### 🚀 The Goal
Build a high-throughput **In-Memory Limit Order Book** matching engine coupled with an **Asynchronous Event-Driven Risk Monitor** (using Go, Python's `asyncio`, or Rust).

---

### Phase 1: High-Performance Limit Order Book (LOB)

Instead of sorting lists on every trade (which is $O(N \log N)$), implement a production-grade LOB using:
* **Binary Search Tree (or Balanced Tree)** for sorting price levels ($O(1)$ lookup for best bid/ask, $O(\log M)$ insertion/deletion).
* **Doubly Linked List** at each price level to store individual limit orders in FIFO (time priority) order ($O(1)$ append/pop).
* **Hash Map** mapping `order_id` to the nodes in the linked list for instant cancellation ($O(1)$ deletion).

#### Requirements:
* Must support:
  * `add_order(id, symbol, price, quantity, direction)` (BUY/SELL)
  * `cancel_order(id)`
  * `get_depth()` -> returns the top 5 levels of bids and asks.
* When an incoming BUY order matches a pending ASK order (or vice versa), execute a trade:
  * Adjust quantities.
  * Emit a `TradeEvent(price, quantity, timestamp)`.

---

### Phase 2: Reactive Async Risk & Exit Monitor

Build an asynchronous, event-driven engine that consumes `TradeEvent` streams and monitors client positions without polling.

#### Requirements:
1. **Concurrency Model**:
   * Run the LOB in a dedicated event loop or thread.
   * Run the **Risk Monitor** in a separate thread/coroutine communicating via an **asynchronous queue** (e.g. `asyncio.Queue` in Python, or channels in Go).
2. **Dynamic Risk Control**:
   * The Risk Monitor registers client positions with specific **Stop Loss (SL)** and **Take Profit (TP)** values.
   * As `TradeEvent`s are pushed into the queue, the Risk Monitor must evaluate open positions against the execution price.
   * If a threshold is hit, the Risk Monitor must immediately generate and submit an offsetting **Market Order** to the LOB to exit the position.
3. **Graceful Handling of Volatility**:
   * Implement **Slippage Accounting**: If the market moves too quickly and the order book lacks liquidity at your target exit price, the system must calculate and report the "Slippage Loss" (the difference between the target SL and the actual filled price).

---

### Phase 3: The CLI & Live Metrics Dashboard

Create a terminal dashboard or minimal CLI dashboard that displays:
1. **The Bid/Ask Spread**: Updated in real-time as orders match.
2. **Current Portfolio Valuation & Margin**: Updated dynamically based on matching events.
3. **Execution logs**: Printing alerts whenever a position hits its Stop Loss or Take Profit and is automatically liquidated by the Risk Monitor.

---

## 🛠️ Step-by-Step Implementation Guide

Follow this sequence to design and build your system:

### Step 1: Design the Core Structures
1. Define your `Order` node structure (attributes: `order_id`, `price`, `qty`, `direction`, `prev_node`, `next_node`).
2. Design a `PriceLevel` doubly linked list containing `head`, `tail`, and helper methods `append(order)`, `remove(order)`.
3. Design the `OrderBook` class mapping:
   * `order_map: Dict[str, Order]` (for $O(1)$ pointer retrieval).
   * `bids: SortedDict` (sorted descending by price).
   * `asks: SortedDict` (sorted ascending by price).

### Step 2: Implement Matching and Execution Logic
1. Inside `add_order`:
   * If the order is a BUY: Check if `price >= minimum ask price`. If yes, match the order with the oldest ASK at that level. Repeat until filled or no matching ASKs remain. If there's a remainder, append it to `bids`.
   * If the order is a SELL: Check if `price <= maximum bid price`. If yes, match with the oldest BID. If there's a remainder, append to `asks`.
2. Generate a transaction log whenever matching occurs.

### Step 3: Implement the Async Event Loop & Queue
1. Initialize an asynchronous queue (e.g., `asyncio.Queue`).
2. Whenever a matching event happens in the order book, push a `TradeEvent` dict into the queue.
3. Create a consumer loop (`async def monitor_risk_loop()`) that waits for incoming events: `await queue.get()`.

### Step 4: Write the Risk Monitor
1. Maintain a dictionary of open positions containing: `current_qty`, `average_entry_price`, `stop_loss`, `take_profit`.
2. When a `TradeEvent` is read, update the current market price for that symbol.
3. Check if the price crossed the Stop Loss or Take Profit bounds:
   * If breached, call the order book's matching logic directly with a counter-direction **Market Order** to liquidate, and print the fill details to the CLI.

---

## 📚 Recommended Resources

### 1. Data Structures & Algorithms
* **"How to Build a Fast Limit Order Book"** by WK Selph: The seminal article detailing the Doubly Linked List + Binary Search Tree approach.
* **`sortedcontainers` (Python Library)**: A pure-python implementation of sorted lists, dicts, and keys. Use it to keep your price levels ordered dynamically without manual sorting.

### 2. Concurrency & Event Handling
* **Python `asyncio` Docs**: Study `asyncio.Queue` and coroutine orchestration.
* **Go Channels**: If writing in Go, read the Go Tour's concurrency section covering channels and select blocks.
* **"Designing Data-Intensive Applications" (Chapter 11: Stream Processing)** by Martin Kleppmann: Excellent guide on event streams, consumers, and state handling.
