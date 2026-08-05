# FutureEdge — Implementation Plan for Claude Code

> Consolidates three prior audit/design documents into one ordered, checkable task list.
> Work top to bottom — later phases assume earlier ones are done. Check off tasks as you
> complete them (`- [x]`) so progress survives across sessions. Each task has a concrete
> "done when" so you know when to stop and move on, not just what to change.
>
> Source docs (bring these into context if you need the full reasoning behind a task,
> not just the instruction): `PRODUCTION_READINESS_AUDIT.md`,
> `FUTUREEDGE_CODE_DESIGN_LOGIC_SECURITY_PROFIT.md`, `INVESTING_MODE_DESIGN_SPEC.md`.

---

## Phase 0 — Stop the bleeding (do these first, small and self-contained)

- [x] **Fix the broken frontend build.** ~~`frontend/src/lib/api.ts` ... does not exist. Create it~~ — the file existed and was complete; it was **gitignored**. The Python-packaging `lib/` pattern in `.gitignore` (unanchored) also matched `frontend/src/lib/`, so `api.ts` was never committed and every fresh checkout failed on the missing import. Fixed by anchoring the pattern (`/lib/`, `backend/lib/`). A second, unrelated build failure was also fixed: `useWebSocket.ts` reads `status`/`run_id`/`hitl_status` off the `hitl_pending` payload, but `HITLPendingMessage` only modelled the proposal shape, not the resolution shape the backend also publishes on that channel.
  **Done when:** `cd frontend && npm run build` succeeds. ✅ builds clean; `npm run type-check` exits 0.
  **⚠️ Not yet committed:** `git add frontend/src/lib/` is still required — the fix only makes the file *trackable*.

- [x] **Add a frontend CI job.** Added a `frontend` job to `.github/workflows/ci.yml`: `npm ci` → `npm run type-check` → `npm run build` on Node 20. Lint was skipped deliberately — there is no ESLint config in the repo, so `next lint` would prompt interactively and hang CI.
  **Done when:** CI fails if `npm run build` fails. ✅ — and because it runs on a clean checkout, it would have caught the gitignore bug above.

- [x] **Fix the kill switch to fail closed.** `duration_seconds` now defaults to `None` (no expiry). Added `kill_switch_state`, a single-row durable table, plus `reconcile_kill_switch_from_db()` wired into `runtime.py::lifespan` before any trading machinery starts. Also: **all three call sites were passing `duration_seconds=14400` explicitly**, so changing only the default would have fixed nothing — the operator halt endpoint, the daily-loss-cap halt in `exit_monitor.py`, and the position-reconciliation halt in `position_reconciler.py` all now halt with no expiry. `is_trading_halted()` treats an unreachable Redis as HALTED, and `execution_agent` + the status endpoint now go through it instead of reading the Redis key directly.
  **Done when:** activating the kill switch with no explicit duration stays halted indefinitely, and restarting the Redis container while halted does not resume trading. ✅ covered by `tests/services/test_kill_switch.py` (11 tests, one per fail-open regression). ⚠️ Not yet exercised against live containers — Docker wasn't running locally.

- [x] **Fix the hardcoded Postgres password.** `docker-compose.yml` now reads `${POSTGRES_PASSWORD:?...}` (fail-fast if unset) plus `${POSTGRES_USER:-futureedge}` / `${POSTGRES_DB:-futureedge}`, and the healthcheck uses the same user. `POSTGRES_USER`/`POSTGRES_DB` were hardcoded too — left alone they'd have drifted from the backend's `.env` the moment either changed.
  **Done when:** changing `POSTGRES_PASSWORD` in `.env` and running `docker compose up -d db` actually changes the running Postgres password. ✅ wiring verified via `docker compose config`. Two caveats: the service is named `postgres`, not `db`; and Postgres only applies `POSTGRES_PASSWORD` when initialising an *empty* data dir, so an existing `postgres_data` volume needs `ALTER USER` or a volume drop.
  **⚠️ The value in `.env` is still literally `futureedge`** — the plumbing is fixed, the weak credential is not.

### Found during Phase 0 — not in the original plan, needs your call

- [ ] **CI has never actually run the backend tests.** The `test` job used `working-directory: backend` with `pytest tests/`, but the suite lives at repo *root* `./tests` — the step died on "file or directory not found". Fixed the path as part of Phase 0 (the suite now collects 74 tests), but the coverage gate (`--cov-fail-under=40`) has never run and is unverified — `pytest-cov` isn't installed locally.
- [ ] **`ruff check app/ --select E,F` reports 64 errors on `main`** (66 before Phase 0), so the lint step fails before tests even start. Mostly `F401` unused imports, 53 auto-fixable. Left alone deliberately: a repo-wide `ruff --fix` sweep is its own reviewable change, not something to bury in Phase 0.
- [ ] **HITL proposals never carried their stop-loss/take-profit to the frontend.** `orchestration_agent._publish_results` computed SL/TP but omitted both from the published payload, so the approval modal always rendered "Stop Loss: N/A" — a human was approving trades without seeing where they exit. Added both to the payload (2 lines) since the frontend type fix depended on it. The same payload still omits `memories`, so the modal's episodic-memory section is always empty.
- [ ] **`CLAUDE.md` documents two commands that don't work:** `cd backend && pytest tests/` (wrong directory, per above) and `docker compose up -d db redis qdrant` (the service is named `postgres`, not `db`).

---

## Phase 1 — Security on the Zerodha auth path

- [x] **Lock down the Zerodha OAuth endpoints.** `login-url` and `status` now take `Depends(require_risk_manager)`, matching the sibling `POST /auth/broker/select`. **The callback deliberately does NOT get a JWT dependency** — it is reached by a browser redirect from Zerodha, which carries no `Authorization` header, and this app has no cookie session to fall back on, so `Depends(require_admin)` there would simply break the OAuth flow. The single-use `state` below is what authenticates the callback instead. No frontend change was needed: the dashboard already calls the risk-manager-gated `/auth/broker/select` on mount, so any user reaching these controls already holds the role.
  **Done when:** hitting these routes without a valid JWT returns 401/403. ✅ — asserted structurally (`test_login_url_and_status_require_auth`) so deleting the dependency fails a test rather than silently reopening anonymous broker access.

- [x] **Add CSRF `state` protection to the OAuth flow.** New `backend/app/services/oauth_state.py`: `issue_state()` mints `secrets.token_urlsafe(32)` bound to the calling user, stored in Redis under `futureedge:zerodha:oauth_state:{state}` with a 600s TTL; `consume_state()` burns it via `GETDEL` so validate-and-burn is atomic and a replay finds nothing. Verified **before** `generate_session()`, so an unsolicited callback never spends the API secret. Kite round-trips the state via its `redirect_params` mechanism, and the callback accepts it either flattened onto the query string or still packed in `redirect_params` — Kite's behaviour here depends on the app's redirect config, and guessing wrong would have failed open.
  **Done when:** a `callback` with missing/mismatched `state` is rejected before `kite.generate_session(...)`. ✅ — every failure mode (missing, unknown, replayed, Redis unreachable) rejects; the two callback tests assert `KiteConnect` was never even constructed.

- [x] **Wire the existing encryption module into the real token path.** **The plan named 2 call sites; there were 5.** Encrypting only in `zerodha_router` would have left plaintext tokens written by `telegram_router.py` (the `/zerodha` bot command) and `scripts/generate_zerodha_token.py`, and would have *broken the live tick feed* — `data/feed.py` reads the token from Redis/JSON in two more places and would have handed ciphertext to `KiteTicker`. All five now go through `encrypt_token()` / the new `decrypt_stored_token()` helper in `token_manager.py`. The env-var fallback (`settings.ZERODHA_ACCESS_TOKEN`) stays plaintext by design — it is the manual operator path and `.env` is gitignored.
  **Done when:** the Redis key returns ciphertext, not a raw Kite token, and the broker still connects end-to-end. ✅ asserted on both sinks (Redis + `broker_token.json`). ⚠️ **Not yet exercised against a live Kite session** — Docker wasn't running locally, so the end-to-end reconnect is covered by mocks only.
  One thing worth knowing: `decrypt_stored_token()` falls back to treating an undecryptable value as legacy plaintext, so a session stored before this change keeps working until it expires at the next 6 AM IST rollover rather than bricking live trading — it is a read-side shim only, nothing writes plaintext. `ENCRYPTION_KEY` is already set to a valid 64-char hex key in `.env`, so the all-zeros dev fallback in `_get_key()` is not in play.

- [x] **Fix IP-based rate limiting for reverse-proxy deployments.** `rate_limiter.py` now resolves the client IP through `get_client_ip()`, gated on a new `TRUSTED_PROXY_IPS` setting. Note the plan's suggested fix (`--proxy-headers`) is only half of it and is unsafe alone: trusting `X-Forwarded-For` unconditionally is *worse* than the original bug, because a client can then rotate the header per request and never hit a limit at all. So the header is honoured only when the socket peer is a configured proxy, and we take the right-most non-proxy entry — the only position an upstream client cannot forge. Default (`TRUSTED_PROXY_IPS=""`) is direct exposure: header ignored entirely. `--proxy-headers` added to the Dockerfile CMD and the compose command; uvicorn's `--forwarded-allow-ips` reads the `FORWARDED_ALLOW_IPS` env var and defaults to `127.0.0.1`, so nothing is trusted until configured.
  **Done when:** rate limiting still keys per real client IP behind nginx/Caddy/Traefik. ✅ 11 tests covering both directions (spoof rejected, real client recovered).
  ⚠️ **Deployment still needs two things from you** when you put a proxy in front: set `TRUSTED_PROXY_IPS` *and* `FORWARDED_ALLOW_IPS` to the proxy IP, and configure the proxy to **strip** inbound `X-Forwarded-For` — for nginx that is `proxy_set_header X-Forwarded-For $remote_addr;`, **not** `$proxy_add_x_forwarded_for`, which appends to whatever the client sent.

### Found during Phase 1 — not in the original plan

- [x] **The callback's failure page rendered `str(e)` into the HTML.** That handler wraps the session exchange, so the exception can carry request/response fragments, and the page lands in browser history. Now logged server-side only; the page shows a generic message. (Also deduplicated three near-identical inline HTML error blocks into one `_error_page()` helper.)
- [x] **`scripts/generate_zerodha_token.py` printed the raw access token to stdout** — live broker credentials into shell history and any CI log. Removed; it now confirms storage without echoing the token. This was a direct violation of the `CLAUDE.md` "never log an access token in plaintext" rule.
- [ ] **No `.env.example` exists**, so `ENCRYPTION_KEY`, `TRUSTED_PROXY_IPS`, and `FORWARDED_ALLOW_IPS` are undiscoverable without reading `config.py`. Worth adding alongside the Phase 0 note that `POSTGRES_PASSWORD` is still literally `futureedge`.

---

## Phase 2 — Execution-safety (money-moving code paths)

- [x] **Fix the HITL resume race condition.** Fixed with an atomic compare-and-set claim rather than the plan's suggested `SELECT ... FOR UPDATE`: a conditional `UPDATE ... WHERE status='HITL_PENDING'` flips the row to a new `HITL_RESOLVING` state and commits **before** `graph.ainvoke()` runs; only the request whose UPDATE matched a row proceeds, the loser gets a clean 400. `FOR UPDATE` would have held a row lock across the entire graph run *including the broker network call*, and would have queued the second request rather than rejecting it — it would eventually see COMPLETED and 400, but only after blocking for the duration of a live order.
  **Done when:** two concurrent resumes for one `thread_id` execute exactly once; the second gets a 400. ✅ `tests/api/test_hitl_resume_race.py` (6 tests, driven through `asyncio.gather` on a shared session so the two callers genuinely contend).
  Worth knowing:
  - The window was much wider than "a double-click": `ainvoke()` runs the whole execution agent **and** the broker round trip, and it sat between the status check and the status write.
  - On graph failure the run is marked `FAILED`, **not** reverted to `HITL_PENDING`. The exception can be raised after the order was already placed, so making it resumable again would risk the exact double-execution this fix prevents. A stranded run needs a human to check the broker; a duplicated order costs real money.
  - `hitl_reviewed_by` / `hitl_decided_at` are stamped by the claim itself, so the audit trail survives a later crash.
  - Verified there is only one production resume path (`workflow_router`); `scripts/test_resume.py` is a dev script and the Telegram HITL buttons do not call the graph directly.

- [x] **Add Kite-specific exception handling.** New `backend/app/brokers/kite_errors.py` classifies every Kite failure into one of three actions, wired into the 5 broker call sites that matter (`zerodha.py::connect` + `place_order`, `exit_monitor`'s exit order, and `execution_agent`'s consensus-exit / scale-in / reversal-exit paths). The other bare `except Exception` blocks were left alone deliberately — they wrap DB writes, Redis publishes and Qdrant calls, not broker calls, and sweeping them in would have buried the change.
  - `RETRY` — `NetworkException`, `DataException` (transient Kite↔OMS). Silent.
  - `FAIL` — `InputException`, `OrderException`, `GeneralException`. Alert once, don't retry, keep trading.
  - `HALT` — `TokenException`, `PermissionException`. Trip the kill switch (`duration_seconds=None`, so it persists until a human releases it) + Telegram alert.
  **Done when:** an invalid/expired token halts trading and alerts instead of retrying indefinitely. ✅ `tests/brokers/test_kite_errors.py` (15 tests).
  Three things worth knowing:
  - The static-IP rejection is matched on **message** as well as exception class. Kite has moved that error between `error_type`s, and classifying it as retryable is precisely what produced the 2026-08-05 retry storm.
  - Unknown/non-Kite exceptions default to `FAIL`, never `RETRY` — on a money path, loud-and-stopped beats quiet-and-looping.
  - **Alert-noise fix found while wiring this up:** `connect()` raised `ValueError` when no token was stored, which the new classifier would have turned into a Telegram alert on *every* portfolio fetch and status poll. Since Kite tokens expire 6 AM IST daily, that is the normal state every morning — it would have paged continuously until login and trained the operator to ignore alerts. `connect()` now returns quietly in that case; only real broker errors reach the classifier.
  ⚠️ **Operational consequence:** an expired token now HALTS trading. The kill switch fails closed, so after re-authenticating each morning you must also **release the kill switch** from the dashboard. That is intended, not a bug.

- [ ] **Batch `get_ltp` calls.** `BrokerBase.get_ltp` takes one symbol; `exit_monitor.py` calls it once per open trade, sequentially, every 10 seconds. Change the interface to accept a list of symbols and use `kite.ltp([...])` (Kite supports batched quotes) so N open positions cost one HTTP call, not N.
  **Done when:** `exit_monitor` fetches all open-trade prices in a single broker call per cycle; update the mock/paper broker to match the new interface.

- [ ] **Add an NSE holiday calendar to `is_market_open()`.** `backend/app/brokers/symbol_mapper.py::is_market_open` only checks weekday + clock time, so it returns `True` on NSE holidays. Add a static list of this year's (and ideally next year's) NSE trading holidays and check against it.
  **Done when:** `is_market_open()` returns `False` on a known holiday that falls on a weekday.

### Found during Phase 2 — hit live on 2026-08-05, both fixed

Both surfaced from one real incident: a live SHORT on HDFCBANK that Zerodha
rejected with *"No IPs configured for this app"* (Kite gates order placement
behind a static-IP allowlist; read-only calls and the tick feed are not gated,
so everything looked connected). No money moved, but two bugs came out of it.

- [x] **A rejected order was recorded as an OPEN position.** `execution_node` built a `trade_record` dict containing `"status": "FAILED"` and then called `TradeRepo.save_trade()` *without passing it* — the dict was dead code, and `save_trade` defaults status to `"OPEN"`. So a refused order wrote an open position with a NULL `broker_order_id`, and `exit_monitor` spent an hour trying to stop-loss a phantom short every few seconds. Reachable by **any** broker rejection, not just the IP case: expired token, insufficient margin, bad symbol. Fixed by passing `status="FAILED"` on the failure path.
  **Done when:** a broker-rejected order never writes `status=OPEN`. ✅ `tests/agents/test_failed_order_not_open.py` — pins both directions, since suppressing real positions would be the worse bug. The one phantom row in the live DB was corrected to `FAILED` (not deleted — audit trail).

- [x] **The fill/cancel race in `zerodha.py::place_order`** (predicted while auditing the order path, then fixed before it could bite). `place_order` polls for 2s, calls `cancel_order`, and *used to return `success=False` unconditionally* — it never checked whether the cancel actually worked. An order that fills in the window between the last poll and the cancel leaves a REAL position at the broker with no trade row, no stop-loss, and nothing tracking it until the ~3:20 PM MIS square-off. This is the dangerous inverse of the phantom bug: that one was loud, this one is silent. Now re-reads the broker after the cancel attempt and trusts that instead of the assumption — including **partial fills**, which Kite reports as `CANCELLED` with `filled_quantity > 0` (real exposure hiding under a cancelled status), and an explicit `UNKNOWN` status when verification itself fails, so an unverifiable order never reads as flat.
  **Done when:** an order filled during the cancel window is reported as a position. ✅ `tests/brokers/test_zerodha_fill_cancel_race.py` — 6 tests, including an `order_history.call_count == 11` assertion so the test cannot pass against the old code path.

**Note for the Kite-exception task above:** the "No IPs configured" failure is exactly the case that task describes. It was swallowed by a bare `except Exception` in `exit_monitor`, which is why it retried indefinitely instead of alerting once and stopping. Worth doing that task next — the incident is a ready-made test case.

---

## Phase 3 — Infra hardening

- [ ] **Backend Dockerfile:** remove `--reload` from the production `CMD`, add a `USER` directive so the process doesn't run as root, add a `HEALTHCHECK`.
- [ ] **docker-compose.yml:** add `restart: unless-stopped` to the `backend` service (currently the only service without a restart policy), pin `qdrant/qdrant:latest` to a specific version tag, add memory/CPU limits.
- [ ] **Redis persistence:** add `command: redis-server --appendonly yes --appendfsync everysec` and a volume to the `redis` service so agent weights/calibration state survive a restart.
- [ ] **Replace the CI backtest-regression placeholder** (currently `sys.exit(0)` unconditionally) with a real check against a minimum Sharpe ratio on a fixed historical window.

**Done when (all of the above):** a fresh `docker compose up` from a clean checkout runs the full stack with no dev-only flags, backend restarts automatically after a crash (`docker kill futureedge_backend` then observe it come back), and CI actually fails when the backtest regresses.

---

## Phase 4 — Investing Mode (new feature)

Full design reasoning is in `INVESTING_MODE_DESIGN_SPEC.md` — this is the condensed build order.

- [ ] **Add dependencies.** `beautifulsoup4` and `lxml` to `backend/requirements.txt` (needed by the fundamentals scraper below).

- [ ] **Drop in the three files already built** (included in this patch):
  - `backend/app/data/fundamentals_scraper.py` — Screener.in scraper (parses top ratios, Pros/Cons flags, quarterly/annual P&L, balance sheet, cash flow, ratios trend, shareholding pattern tables)
  - `backend/app/agents/fundamental_agent.py` — turns scraped data into a scorecard (DuPont breakdown, red/yellow/green flags, verdict, LLM narration with template fallback)
  - `backend/app/db/models/fundamental_scorecard.py` — storage model
  **Done when:** `await generate_scorecard("RELIANCE")` returns a populated `FundamentalScorecard` against a live Screener fetch. Note the parser selectors were checked against a live page fetch but Screener can change markup — if sections come back empty, view-source a company page and diff against `EXPECTED_SECTION_IDS` in the scraper first.

- [ ] **Register the new model** in `backend/app/db/models/__init__.py` (or wherever models are aggregated for Alembic/`create_tables`), then run `python -m app.scripts.create_tables` (or generate a proper Alembic migration if the DB already has data you care about).

- [ ] **Add `analysis_mode` to `WorkflowRun`.** New column, `"TRADING" | "INVESTING"`, defaulting to `"TRADING"` for backward compatibility. Thread it through `RunWorkflowRequest` in `workflow_router.py`.

- [ ] **Add CNC product-type support.** `brokers/zerodha.py::place_order` currently hardcodes `product = self._kite.PRODUCT_MIS`. Make this mode-dependent: `MIS` for `TRADING`, `CNC` for `INVESTING`. Thread a `product_type` param through `execution_agent.py`.

- [ ] **Wire the Fundamental Agent into the graph for Investing mode.** In `graph/builder.py`, add a conditional branch: if `analysis_mode == "INVESTING"`, run `fundamental_agent.generate_scorecard()` as the primary decision input instead of `signal_agent`; still run the technical signal agent but only as an entry-timing check (e.g. "don't buy if RSI > 70"), not the primary signal.

- [ ] **Force HITL always-on for Investing mode**, regardless of `HITL_AUTO_APPROVE_ENABLED` — this is a hard rule (see `CLAUDE.md`), not a config default. A long-horizon capital allocation decision should never auto-execute.

- [ ] **Core-satellite position sizing for Investing mode.** Replace Kelly/ATR sizing (built for high-frequency trading, not buy-and-hold) with `suggested_allocation_pct` from the scorecard applied against a configurable long-term capital pool (`INVESTING_CORE_CAPITAL` setting), not the same equity base used for intraday Kelly sizing.

- [ ] **Skip `exit_monitor` for Investing-mode trades.** These aren't same-day exits. Instead, add a scheduled job (reuse the `apscheduler` pattern already in `jobs/scheduler.py`) that re-runs `generate_scorecard()` quarterly per held Investing-mode symbol and alerts (Telegram) if the verdict degrades from `INVESTMENT_GRADE`.

- [ ] **Set up the separate core-holdings watchlist**, distinct from the trading watchlist: `RELIANCE, TCS, HDFCBANK, INFY, HINDUNILVR, ICICIBANK, ITC, LT, BAJFINANCE, ASIANPAINT, MARUTI, SUNPHARMA` — add as `INVESTING_WATCHLIST_SYMBOLS` in `config.py`, separate from the existing `WATCHLIST_SYMBOLS`.

- [ ] **API route:** `GET /api/v1/fundamentals/{symbol}/scorecard` (viewer+ access) returning the latest scorecard, generating one on-demand if none cached within `FUNDAMENTALS_CACHE_SECONDS`.

- [ ] **Frontend:** a scorecard view component rendering the verdict, flag groups, and DuPont breakdown — mirrors the format in `INVESTING_MODE_DESIGN_SPEC.md` §4. Lower priority than the backend pieces above; fine to stub with the raw JSON initially.

- [ ] **News/events upgrade (can happen in parallel with the above):**
  - Fix `news_fetcher.py` so `fetch_news(symbol=...)` actually filters articles by company name match, not just cache-key namespacing (currently every symbol gets the same generic feed).
  - If the MarketPulse AI course project's NSE/BSE corporate-announcement scraper is far enough along, reuse its output as a `corporate_events` feed here rather than building a second scraper — this is a genuine shared component, not duplicate work.

---

## Order of operations summary

Phase 0 → Phase 1 → Phase 2 → Phase 3 can mostly happen in parallel with each other once Phase 0 is done (they touch different files). Phase 4 depends on nothing above except general codebase stability, but do NOT skip Phase 2's HITL-race-condition fix before adding Investing mode's own always-HITL execution path — same underlying mechanism, fix it once.
