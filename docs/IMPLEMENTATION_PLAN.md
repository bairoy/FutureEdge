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

- [x] **Backend Dockerfile:** `--reload` removed from the production `CMD` (docker-compose keeps it as an explicit dev override for the bind-mounted source); added a non-root `USER` (uid 1000) and a `HEALTHCHECK` hitting `/health`. Note `/app` is chowned because the OAuth callback writes `broker_token.json` there — and that chown governs the built image only, since compose bind-mounts `./backend` over `/app` and the host's ownership wins in dev.
- [x] **docker-compose.yml:** `restart: unless-stopped` on `backend` — it was the only service without a policy, and it runs `exit_monitor`, so a crash left real MIS positions with no stop-loss until the broker's ~3:20 PM square-off. `unless-stopped` rather than `always` so a deliberate `docker compose stop` stays stopped. Qdrant pinned to `v1.18.2` (was `:latest` — episodic trade memory lives there and a storage-format change is not something to meet during market hours). Memory limits added as runaway-leak ceilings, set well above steady state: an OOM-killed backend is the exact failure the restart policy exists to prevent.
- [x] **Redis persistence:** `--appendonly yes --appendfsync everysec` plus a `redis_data` volume. Adaptive agent weights and calibration state were being discarded on every restart. (The kill switch was never at risk — Phase 0 made it durable in Postgres, which is why it survived a restart during the 2026-08-05 incident.)
- [x] **Replace the CI backtest-regression placeholder.** Now runs `app/scripts/run_regression_backtest.py`. Two findings changed the design:
  - **The strategy produces ZERO trades on NIFTY 50** — the symbol the placeholder named — on both daily and 1-minute bars. Gating on it would have been a check that could never fail. Uses RELIANCE instead.
  - **A fixed date range cannot be fetched live.** This is an intraday strategy (0 trades on daily bars), and yfinance only serves 1-minute data for ~30 days. So the gate runs against `backend/app/data/fixtures/regression_candles.json`, a committed slice of real 1-minute RELIANCE candles. The input never changes, so a Sharpe change means the *strategy* changed — and CI needs no network for the step. `run_backtest` gained an optional `candles` param (backward compatible) to make this possible.
  - ⚠️ **The measured baseline Sharpe is −83.57** (1832 candles, 213 trades, 9.4% win rate, −1.81% return). **The strategy loses money on recent intraday data after modelled costs.** The floor is set at −150 as a tripwire for code regressions (inverted comparison, lookahead bias, broken exits), NOT as a claim of profitability. Improving this is strategy work, tracked separately from the CI gate.

**Done when (all of the above):** ✅ verified live — `docker compose up` brings all five services healthy, `docker inspect` confirms `restart_policy=unless-stopped`, `appendonly yes` / `appendfsync everysec`, `qdrant/qdrant:v1.18.2`, and a 3G backend limit. The regression gate runs green locally against the fixture.
⚠️ **Crash recovery is config-verified, not behaviour-verified.** Note the plan's own suggested test (`docker kill futureedge_backend`) does NOT work: Docker's restart manager treats an explicit `kill` (and `docker compose stop`) as user intent and will not restart the container — observed here, `RestartCount` stayed 0. A valid test has to make the process die on its own, e.g. `docker compose exec backend sh -c 'kill -9 1'`.

---

## Phase 4 — Investing Mode (fundamental analysis, advisory-only)

> **Revised 2026-08-19** after a full design pass against `fundamental_analysis_steps.md`
> (the three-stage method this phase implements) and `Fundamental_Analysis_Study_Notes.pdf`.
> The earlier version of this phase assumed Investing mode would *place CNC orders*.
> It will not. **Investing mode never places an order.** It produces research; the human
> reads it and executes manually at the broker.
>
> Removed from the earlier plan and deliberately NOT to be built: CNC product-type support
> in `zerodha.py`, core-satellite position sizing, `INVESTING_CORE_CAPITAL`,
> `suggested_allocation_pct`, and an always-HITL investing execution path. There is no
> investing execution path to gate. The `CLAUDE.md` hard rule stands unchanged and is now
> enforced structurally (see the execution guard task in 4B).
>
> Design reasoning: `fundamental_analysis_steps.md` (repo root) is the authoritative
> three-stage method; `Fundamental_Analysis_Study_Notes.pdf` has the thresholds and the
> reasoning behind them. Note `INVESTING_MODE_DESIGN_SPEC.md`, cited by the old version of
> this phase and by the header above, **does not exist in `docs/`** — neither do the other
> two source docs the header names. Don't go looking for them.

**The shape, in one picture:**

```
START ── route_by_analysis_mode ──┬── "TRADING"   → regime_agent → [signal ∥ sentiment ∥ risk
                                  │                  ∥ portfolio ∥ macro] → orchestrator
                                  │                  → (human_review) → execution → END
                                  │
                                  └── "INVESTING" → data_fetch → [business ∥ financial
                                                     ∥ valuation] → thesis_agent → END

Document ingestion (annual reports, concalls) is NOT in this graph — it runs
out of band as a scheduled job and writes to Qdrant. The business agent reads
what it left behind. See the ingestion task in 4C for why.
```

Two disjoint branches. No shared tail — Investing has no execution to reuse.

---

### 4A — Data foundation (do this first; everything else reads from it)

- [x] **Add dependencies.** `beautifulsoup4`, `lxml` added to `backend/requirements.txt`. `bs4` was already installed in the venv; `lxml` genuinely was not — `_parse_page` calls `BeautifulSoup(html, "lxml")`, so the scraper could not have run.

- [x] **Fix the six known defects in the existing groundwork files** before wiring anything to them. These were found by checking `fundamental_agent.py` against live Screener pages and against the Varsity notes; all six are silent-wrong-answer bugs, not crashes:
  1. `_latest_period()` returns `"TTM"`, which exists in the P&L table but **not** in the balance sheet — so `if latest in raw.balance_sheet` is always False and **DuPont never computes, Debt/Equity is never produced.** Pick the latest period present in *both* tables.
  2. Interest coverage uses `Operating Profit / Interest`. Screener's "Operating Profit" is **EBITDA**; the correct numerator is **EBIT = EBITDA − D&A**. Current code overstates coverage.
  3. DuPont uses period-end assets and equity. The method specifies **averages**: `(opening + closing) / 2`.
  4. P/E thresholds flag yellow only above 60×. The notes say avoid beyond **25–30×**.
  5. ROE threshold is 15%; the notes use **>18% general / >25% for the 10-point checklist**.
  6. `_parse_top_ratios` silently drops **Market Cap** — the `Cr.` suffix breaks `_parse_number`.
  **Done when:** a scorecard for RELIANCE contains a non-null DuPont breakdown and Debt/Equity, and unit tests cover each of the six. ✅ verified against a live fetch — RELIANCE now returns DuPont (margin 9.07%, turnover 0.512x, equity multiplier 2.36x) and Debt/Equity 0.446, both of which were absent before. 42 tests in `tests/agents/test_fundamental_agent.py` + `tests/data/test_fundamentals_scraper.py`.
  Two fixes went slightly beyond the six listed, both for the same class of bug:
  - **Zero interest was treated as missing data.** `if op_profit is not None and interest:` skips a debt-free company, so the best possible coverage result produced no flag at all and *lowered* the confidence score. Now flags GREEN explicitly.
  - **Composite cells parsed as a single number.** `"High / Low ₹ 1,612 / 1,250"` holds two values; `_parse_number` now returns None rather than one half of a range.
  ⚠️ **Raising the ROE/ROCE bars changes existing verdicts.** RELIANCE now scores `AVOID` — ROE 8.91% and ROCE 10.3% both fall below the new 12% "ok" line, where the old 15/10 bars let ROCE through as YELLOW. That is faithful to the source method (>18% ROE), but note the verdict rule is still crude flag-counting over 5 ratios; it gets replaced by the Stage 2 10-point checklist in 4C.

- [x] **Refuse banks and NBFCs rather than scoring them wrong.** Screener serves financials a **different schema**: RELIANCE has `Sales` / `Operating Profit` / `OPM %` / `Borrowings+`, while HDFCBANK and BAJFINANCE have `Revenue` / `Financing Profit` / `Financing Margin %` / `Borrowing` (+ `Deposits`). Every current lookup returns `None` for those. Beyond key-mapping, **Debt/Equity and interest coverage are meaningless for a lender.** Detect the financial schema and return `NOT_RATED` with reason `SECTOR_UNSUPPORTED`.
  **Done when:** `HDFCBANK`, `ICICIBANK`, `BAJFINANCE` return `NOT_RATED / SECTOR_UNSUPPORTED`, not a low score. ✅ verified live on HDFCBANK. Detection reads the P&L row labels (`detect_sector_schema`) rather than a sector name, because the row labels are what actually break parsing and Screener's sector labels are not stable enough to branch on. `Verdict.NOT_RATED` and `not_rated_reason` added to both the dataclass and the DB model.
  **`INVESTING_WATCHLIST_SYMBOLS` does not exist yet** — it is created in 4D, and the list there already excludes the three financials.

- [x] **Add the Screener JSON schedules fetch for cash.** Net Debt needs Cash & Equivalents, which is not in the main balance-sheet table but *is* free at
  `GET https://www.screener.in/api/company/{id}/schedules/?parent=Other+Assets&section=balance-sheet`
  (verified: returns 200 `application/json` with `Cash Equivalents`). Being JSON it is more stable than the HTML parsing. Shares outstanding: derive as `Equity Capital × 1e7 / Face Value`.
  **Done when:** `FundamentalRaw` carries `cash_equivalents` and `shares_outstanding` for RELIANCE, and Step 7 of the DCF can run without a manual input. ✅ live: 12 periods of cash (Mar 2026 = ₹108,179 Cr) and 13,532,000,000 shares. **Net Debt is now computable for free** — this was the one DCF input we thought might need paying for.
  `company_id` is read from `data-company-id` and deliberately NOT regex-matched out of `/company/(\d+)/` URLs — the peer-comparison block contains those for *other* companies, so that pattern would attach a competitor's cash balance to this company's DCF. There is a test for exactly that.

- [x] **New table `fundamental_manual_inputs`** — keyed `(symbol, period, field)`, with `value`, `entered_by`, `entered_at`, `source_note` (e.g. "AR FY26 p.142"). Human-entered values are the **highest**-trust tier, not a fallback, but they must carry provenance or a typo becomes a permanent verdict. Rules: values expire when their `period` is no longer the latest; a manual value that contradicts a scraped one beyond tolerance is **surfaced as a conflict, never silently applied**.
  **Done when:** entering a gross margin for a symbol makes it appear in the next scorecard tagged `source=MANUAL` with its `source_note`. ⚠️ **Table only — the agent does not read it yet.** Wiring the lookup into scoring belongs with the Stage 2 checklist in 4C, since that is what generates the `missing_data` entries these values answer. `value_numeric` + `value_text` so non-numeric answers (subsidiary lists) fit too.

- [x] **New table `investing_holdings`** — the manual holdings register: `symbol`, `quantity`, `avg_buy_price`, `buy_date`, `thesis_snapshot_id`. Without it the system cannot distinguish BUY from ADD from HOLD (see the stance matrix in 4C) and the quarterly thesis-drift job has nothing to watch. You enter these by hand after executing at the broker.
  **Done when:** a symbol in the register renders "owned" state in the stance matrix. ⚠️ **Table only** — the stance matrix itself is 4C and the CRUD endpoint is 4D.

- [x] **Register all new models** in `backend/app/scripts/create_tables.py` — there is **no** `app/db/models/__init__.py`; models are imported explicitly there. Add `FundamentalScorecard`, `FundamentalManualInput`, `InvestingHolding`, then run `python -m app.scripts.create_tables`. ✅ registered and the module imports clean.
  ⚠️ **`create_tables` has NOT been run against a live database** — Postgres wasn't up locally. The tables do not exist yet.
  Incidental fix while here: every import in that block is flagged `F401 imported but unused` by ruff, which **CI gates on** — the imports look dead but importing *is* the registration. Added `# noqa: F401` to all nine (six pre-existing plus the three new) rather than leaving the new ones to add three more failures to an already-red lint job.

---

### 4B — Graph wiring

- [x] **Add `analysis_mode` to `AgentState`** plus the investing keys. Report shapes are Pydantic models in `state.py` (`BusinessReport`, `FinancialReport`, `ValuationReport`, `InvestmentThesis`, `MissingDatum`) alongside the existing `AgentVote`/`TradeProposal`.
  **Done when:** ✅ the three parallel stage agents all append to `missing_data` in one superstep without raising `InvalidUpdateError` — pinned by `test_parallel_stages_all_append_to_missing_data`, which is the only thing that actually proves the reducer is wired. Critical constraint: **each parallel node must write its own top-level key.** If `business` / `financial` / `valuation` all write into one shared dict, LangGraph raises `InvalidUpdateError` on concurrent writes — the same reason the trading branch uses separate `signal_vote` / `sentiment_vote` keys rather than one `votes` dict.
  ```python
  analysis_mode:     str                          # "TRADING" | "INVESTING"
  fundamentals_raw:  Optional[dict]               # data_fetch — single writer
  derived_metrics:   Optional[dict]               # data_fetch — single writer
  business_report:   Optional[BusinessReport]     # Stage 1
  financial_report:  Optional[FinancialReport]    # Stage 2
  valuation_report:  Optional[ValuationReport]    # Stage 3
  investment_thesis: Optional[InvestmentThesis]   # thesis_agent
  missing_data:      Annotated[list[dict], operator.add]   # reducer — all three may append
  ```
  `missing_data` is the one key all three parallel nodes may write, precisely because `operator.add` merges concurrent appends.

- [x] **Branch at `START`** in `graph/builder.py` via `add_conditional_edges(START, route_by_analysis_mode, {...})`. **Default to `"TRADING"` on a missing or unknown value** so existing callers and in-flight checkpoints are unaffected. ✅ six routing cases tested including `None`, unknown strings, and lowercase. The compiled graph now has 16 nodes; the two branches share no node.

- [x] **`data_fetch` is the fan-out point, not Stage 2.** The obvious reading is that Stage 3 depends on Stage 2 (DCF needs FCF, FCF comes from the statements). But both stages read the *same* statements. So `data_fetch` emits raw data **plus pure derived metrics** — FCF series, CAGRs, margins, debtor/inventory days, ROE averages: computation with zero judgment — and Stage 2 and Stage 3 become siblings interpreting shared numbers. Compute once, interpret in parallel. This is what makes the three-way fan-out legal.
  `data_fetch` must be the **only** node that touches the network for financials, and must never run uncached on an interactive request path (Redis cache already exists, 3 days, with a 2s process-wide request gap).
  **Done when:** ✅ verified live on RELIANCE — one fetch, then seven derived metrics (FCF series and 3-year average ₹44,105 Cr, cash by period, 13.53bn shares, both compounded growth tables) read by all three siblings. `_derive_metrics` is deliberately free of thresholds: the moment it decides a number is *good*, Stages 2 and 3 stop being independent readings and start inheriting one opinion.

- [x] **Add `run_investing_cycle(symbol, user_id)`** as a separate entrypoint in `builder.py`, invoking the same compiled graph. `run_agent_cycle()` fetches 1-minute candles and a full `PortfolioSnapshot` before invoking; investing needs the current price and nothing else. Don't teach one function two jobs — the trading entry path stays untouched. ✅ `run_agent_cycle` gained only two lines (`analysis_mode: "TRADING"` and an explicit `symbol`), plus null defaults for the investing keys so the state shape does not change with the mode.

- [x] **Execution guard.** `execution_node` hard-refuses `analysis_mode == "INVESTING"` at the top of the function, regardless of state or approval. The investing branch cannot structurally reach it — this is defence in depth, so "advisory-only" is a property enforced by code rather than by graph topology someone may later edit. **This needs a test** (`execution_agent` is a documented money-moving path per `CLAUDE.md`). ✅ three tests in `tests/graph/test_investing_branch.py`.
  Placement matters and is worth not "tidying" later: the guard sits at **LAYER -1**, before `state["consensus"]` is read and before the kill switch. The investing branch never populates `consensus`, so a guard placed after that line would raise `KeyError` on the order path instead of refusing cleanly — there is a test for exactly that. Ahead of the kill switch because this is not a risk decision that could be waived; it is a mode that has no orders.

- [x] **Do NOT use `interrupt()` for missing data, and do NOT touch `resume_workflow`.** ✅ `resume_workflow` is untouched. The investing branch ends at `thesis_agent` with no `interrupt()` anywhere, and sets `hitl_status="NOT_APPLICABLE"`. The tempting design pauses the workflow when a field is missing and resumes on user input. But `resume_workflow` is one of the two money-moving paths with a documented race-condition history, gated behind `require_risk_manager`. Routing "user typed a gross margin" through it drags a research feature into the most safety-critical code in the repo.
  Instead: `thesis_agent` always completes, marks the report `INCOMPLETE`, and `missing_data` lists what is absent and why. The user fills values into `fundamental_manual_inputs` via a separate endpoint, then triggers a **fresh run** — cheap, because the scrape is Redis-cached. Idempotent, no checkpoint resume, guarded path untouched.

---

### 4C — The three stage agents + thesis

> **The five investing nodes already exist and run** — wired in during 4B so the graph
> shape and the parallel-write contract are proven against something that executes.
> `data_fetch` and `thesis_agent` are real; `business_agent`, `financial_agent` and
> `valuation_agent` are honest stubs that write their own state key and report
> themselves in `missing_data`. Filling them in never has to touch the graph.
> They live together in `app/agents/investing_nodes.py` while they are still structure
> rather than substance; each moves to its own file as it is implemented.

- [ ] **Document ingestion pipeline — do this before `business_agent`, it is what makes Stage 1 automatic.**

  **This supersedes the earlier assumption that Stage 1 is ~70% manual.** That figure came from
  treating "not in Screener's tables" as "not machine-retrievable" — they are different things.
  Verified live on 2026-08-19: Screener's `#documents` section (add it to `EXPECTED_SECTION_IDS`)
  links **annual report PDFs hosted on BSE**, **concall transcripts**, **credit rating reports**
  and **corporate announcements**. The RELIANCE FY2026 annual report is 187 pages / 11 MB and
  extracts as **real text, not scanned images** — p.60 carries labour and collective-bargaining
  disclosure (Q15), p.150 the consolidated-statement notes with subsidiary and share-count
  detail (Q18). **16-17 of the 18 questions are machine-answerable.** Manual entry becomes the
  rare fallback, not the normal path.

  Where each source earns its place:

  | Source | Questions it answers |
  |---|---|
  | Annual report | Q1, 3, 4, 6, 10, 11, 12, 13, 14, 15, 18 — plants, products, segments, subsidiaries, auditors, headcount |
  | **Concall transcripts** | Q5, 7, 10, 11 — capacity utilisation and client concentration are things analysts ask management out loud, and rarely appear in the AR |
  | Credit rating reports | Q4, 14, 18 — debt structure, subsidiary opacity, auditor concerns |
  | Corporate announcements | Q2 — SEBI, legal, regulatory events |
  | Shareholding table (already scraped in 4A) | Q9 — FII/DII/promoter trend, promoter pledge |

  **Ingestion runs out of band, once per document — never inside a run the user is waiting on.**
  A 187-page PDF takes tens of seconds to fetch and extract and longer to embed; an annual report
  is published once a year. Ingest once, cache permanently, and every later analysis is a vector
  query. This is what keeps the "it should be fast" requirement intact while reading whole annual
  reports.

  ```
  INGESTION (one-time per document, apscheduler job)
    Screener #documents -> download -> extract text PER PAGE -> chunk -> embed -> Qdrant
  ANALYSIS RUN (what the user waits on)
    business_agent -> vector query per question -> LLM answers, cites page number
  ```

  **A separate Qdrant collection is required.** The existing `trade_memories` collection is a
  hand-built **12-dimensional numeric market vector** (`app/memory/embedder.py`) — not text
  embeddings. Do not reuse it: different dimension, different payload, different lifetime. Add
  `QDRANT_DOCUMENTS_COLLECTION` alongside `QDRANT_COLLECTION`.

  **Embeddings: OpenAI `text-embedding-3-small` (1536 dims).** Decided after measuring, not
  assumed. Running a model locally was ruled out — this machine cannot host an LLM runtime, and
  `sentence-transformers` pulls ~2 GB of torch into the backend image. Neither cost was warranted,
  because the API cost that supposedly justified them is not real:

  | | Tokens | Approx cost |
  |---|---|---|
  | One annual report (187 pages, measured) | ~285k | — |
  | One company (2 ARs + 4 concalls + 1 rating) | ~615k | — |
  | **Whole 9-symbol watchlist, one time** | **~5.5M** | **~$0.11** |
  | Yearly top-up (1 new AR + 4 concalls each) | ~2.9M | ~$0.06/yr |

  Re-ingesting an already-stored document is a no-op (deterministic chunk ids), so this does not
  recur. ⚠️ Verify current per-token pricing before relying on the figures — they are from a model
  knowledge cutoff, and the token counts assume ~4 chars/token.

  `LOCAL_MODEL_BASE_URL` still takes precedence when set, so a local endpoint can be swapped back
  in as configuration rather than a code change. **Change `EMBEDDING_DIMENSION` with the model** —
  Qdrant fixes vector size at collection creation and a mismatch fails every upsert.

  Chunk with the **page number retained in the payload** — that is what makes every Stage 1 answer
  citable as "AR FY2026, p.142", the same provenance the manual-input table records by hand.

  **Rate-limit BSE the same way Screener is rate-limited** (`_throttle()` in the scraper). These
  are 10 MB+ files and bulk downloading will get the IP blocked.

  **Done when:** ingesting RELIANCE's FY2026 annual report produces retrievable, page-cited chunks
  in Qdrant, and re-running ingestion for the same document is a no-op.
  ✅ **Built** — `app/data/documents.py` (discovery + extraction), `app/memory/document_store.py`
  (chunking, embedding, retrieval), `app/jobs/document_ingestion.py` (the scheduled job). 36 tests.
  Verified live: 40 documents discovered for RELIANCE (15 annual reports, 19 concalls, 6 credit
  ratings), FY2026 annual report extracted to **187 pages at 6,071 chars/page**, and OpenAI
  embeddings returning 1536 dims matching config.
  ⚠️ **Not yet run end-to-end** — Qdrant was not running locally, so no chunks have actually been
  written. `init_document_collection()` also still needs calling from `runtime.lifespan()`
  alongside the existing `init_collection()`.
  Design points worth not undoing: **chunks never span a page** (a chunk covering p.141–142 cannot
  honestly cite either, and an uncheckable citation is not evidence); **concall rows link several
  artefacts and only the Transcript is ingested** (the AI Summary is another party's reading);
  **retrieval filters by symbol** — without it one company's question can be answered from
  another's annual report, which is the worst failure available here: confident, well-cited, wrong.
  ⚠️ Some smaller companies publish scanned-image ARs, which need OCR. Detect zero extracted text
  and fall back to manual input rather than silently ingesting empty pages.

- [x] **`business_agent` — Stage 1, the 18 questions, answered by the system.** Fan out *inside the
  node* with `asyncio.gather` over the question clusters — **not** as extra graph nodes; graph nodes
  are checkpoint units and 18 of them would be unusable in the run history UI. Each question
  retrieves from the document collection above and answers with a page citation.

  **Stage 1 is not a gate.** `fundamental_analysis_steps.md` says "list every red flag found in one
  place", not "stop and do not proceed". All three stages always run and always report. And the
  asymmetry that follows from ingestion being fallible: **a missing answer never blocks; a bad
  answer does.**

  **Two questions stay genuinely hard, and reading harder does not fix either:**
  - **Q2, promoter criminal history / SEBI action.** No annual report says "our promoter has a
    criminal record." This needs external sources and is where name collision lives (below).
  - **Q17, "could this be replicated in a cheap-labour country?"** A judgment, not a lookup. The LLM
    may reason about it from the business description, but the answer must be **labelled an opinion**,
    not stated as a finding.

  **For Q2 only — source hierarchy is regulatory first, open web second, social media never.**
  Indian retail stock discussion is saturated with pump-and-dump groups and coordinated campaigns;
  feeding it into a research gate means reading manufactured content.

  | Q2 sub-question | Free authoritative source |
  |---|---|
  | Enforcement / debarment | SEBI orders + debarred entities list |
  | Directorships, disqualification | MCA21 — DIN lookup, disqualified directors list |
  | Promoter share transactions | NSE/BSE insider-trading disclosures (SAST/PIT — 2-day mandate) |
  | Promoter pledging | BSE/NSE quarterly shareholding pattern (already scraped) |
  | Insolvency history | NCLT / IBBI |

  **Every finding carries `claim`, `source_url`, `source_tier`, `date`, `name_match_confidence`.**
  Unsourced claims cannot influence the verdict — dropped, not downweighted. Three-state outcome:
  **BLOCK** only on a Tier-1 regulatory hit with a confirmed identity match (DIN or exact entity
  name); **FLAG** for press or open web, surfaced to the human, never auto-blocking; **CLEAR**
  meaning *"nothing found"*, never *"clean"* — the confidence field must not let a gate launder
  ignorance into a green light.

  **The failure mode to design against is name collision.** Indian names collide constantly; an LLM
  confidently linking a promoter to a same-named person in an unrelated SEBI order produces a false
  block you never learn about. It is also how a machine-derived defamatory claim ends up in the
  database. This risk is the reason Stage 1 hands over evidence with links rather than deciding
  alone — a weak local model that gives you three links to read is fine; one that silently blocks a
  stock is not.

  **Search tooling, zero cost:** add **SearxNG** to `docker-compose.yml` — self-hosted meta-search,
  no API key, no quota, and the stack already runs five services. `ddgs` (DuckDuckGo) as the lighter
  fallback. The regulatory sources above need no search engine, just direct fetchers.

  **Done when:** all 18 questions return either an answer with a source citation or an explicit
  "not found", and RELIANCE answers at least 15 of 18 from ingested documents alone.
  ✅ **Built** in `app/agents/business_agent.py`, 25 tests. ⚠️ **The 15-of-18 target was not met:
  RELIANCE answers 9.** Every question returns an answer with citations or an explicit NOT_FOUND,
  so the output is honest — but half the checklist still needs better retrieval, more documents,
  or manual entry. Do not read 9/18 as a failure of the approach: it is the real coverage, and the
  earlier design assumed ~30%.

  | | Result |
  |---|---|
  | Answered from documents | **12 of 18** (Q1, 3, 4, 5, 7, 9, 10, 11, 12, 13, 14, 18) |
  | Needs external sources | 1 (Q2 — promoter background) |
  | Not found in ingested docs | 5 (Q6, 8, 15, 16, 17) |

  Reproducible: two consecutive runs gave 12/18 with the identical failing set.

  Real answers, not paraphrase: Q12 returned segment revenue with figures (O2C ₹6,58,991 Cr),
  Q14 named the statutory auditor and its appointment term, Q18 counted 235 subsidiaries.

  **Concall ingestion measurably helps, and the effect is specific.** Adding 3 transcripts took
  RELIANCE from 8 to 9: **Q5 (capacity utilisation) went from NOT_FOUND to answered, cited to
  `Concall Jan 2026, p.23`** — analysts ask about utilisation out loud and the annual report does
  not volunteer it. Q7 (client concentration) did NOT improve, and that is probably correct for a
  conglomerate with no single large customer to disclose.

  **Two honesty defects were found by running it and are now pinned by tests — do not undo either:**
  1. **Status must mean what it says.** Keying ANSWERED off "did we retrieve passages" scored
     RELIANCE **17/18 while eight of those answers said "the excerpts do not provide this"**.
     Completeness gates the verdict downstream, so the inflated count was silently buying a
     verdict the evidence did not support.
  2. **A trailing caveat is not a non-answer.** The first fix matched the disclaimer anywhere in
     the text and threw away Q14 and Q18 for hedging in their last sentence. A fixed character
     window failed the same way. Only the FIRST SENTENCE decides.
  Also: `RED FLAG: None.` is filtered — asked to flag something if present, a model answers the
  instruction literally, and recording that puts a red flag on a clean company.

  **Queries are phrased in the document's language, not the reader's**, and this is measured, not
  stylistic: keyword-style phrasings scored ~0.36 and returned unrelated prose, while phrasings
  echoing the report's own section headings scored ~0.57 and landed on the right table. Each
  question carries several phrasings because one rarely covers a topic the report spreads out.

  **The biggest single win was rewriting queries in MANDATORY-disclosure wording** — 9/18 → 12/18.
  Regulation fixes the phrasing, so it is near-identical across companies: BRSR's "number of
  locations where plants and offices of the entity are situated", Ind AS 108's "revenue from a
  single external customer amounting to 10 per cent or more", Schedule V's "industry structure and
  developments", Rule 8(3)'s "technology absorption". Q4 went from nothing to naming Hazira, Dahej,
  Barabanki, Hoshiarpur, Patalganga, Nagothane and Silvassa. Prefer regulated wording over natural
  wording when adding queries.

  ⚠️ **More retrieved passages made it WORSE — do not "improve" this by raising `STAGE1_TOP_K`.**
  Going 5 → 8 (keeping 2× after the merge) dropped RELIANCE from 12/18 to 10/18, with Q5 and Q7
  regressing from answered to not-found. Extra passages buy recall at the cost of precision, and a
  model handed mostly irrelevant fragments declines to answer rather than digging out the good one.
  Re-measure the answered count before changing it.

  **The five that still fail are a PDF-extraction problem, not a retrieval problem.** Probing
  directly shows retrieval reaching the right pages — p.155 Employee Benefits for Q15, p.47 MD&A
  "Threats" for Q8 — but table-heavy pages extract into chunks that have lost their column headers,
  leaving numbers with no labels that nothing can answer from. Fixing Q6/Q15 properly means
  table-aware extraction, not more or better queries.

  **Synthesis is `gpt-4o-mini`, drafting only from retrieved passages.** With
  `STAGE1_SYNTHESIS_ENABLED=false` the passages themselves are returned, which is a usable Stage 1
  on its own: three cited paragraphs beats a 190-page annual report, and every claim is sourced
  because you read the source.

  **The gate is FLAG, never BLOCK.** A block must rest on a Tier-1 regulatory source with a
  confirmed identity match, and those fetchers do not exist yet — see the Q2 row above.

- [x] **`financial_agent` — Stage 2, the 10-point checklist.** Implement all ten from `fundamental_analysis_steps.md` §Stage 2. **7 of 10 come from Screener**; the rest are retrieved from the ingested annual report (see the ingestion task above) and only route to `missing_data` → manual input when retrieval fails. Notably **Gross Profit Margin (check 1) is not computable** — Screener has no COGS row; use `OPM %` as a labelled proxy and mark the true figure as missing rather than silently substituting.
  Screener *does* provide directly, no derivation needed: `Free Cash Flow`, `CFO/OP`, `Debtor Days`, `Inventory Days`, `Days Payable`, `Cash Conversion Cycle`, `Working Capital Days`, `ROCE %`.
  Implement the four calculation guards from the steps file as explicit code paths, each able to emit a flag: itemised CapEx (not the whole investing-activities line) for FCF; one-off/exceptional-item detection before any CAGR; low-or-negative base-year detection; and an explicit EBITDA-vs-FCF divergence check for companies mid-capex.
  **Done when:** each of the 10 emits `pass | fail | flag | not_computable`, each with its source. ✅ built in `app/agents/financial_agent.py`, 30 tests. Live: RELIANCE and ITC both reach 70% completeness (checks 1, 9, 10 need the annual report), ITC passes ROE at 29.3% where RELIANCE fails at 8.9%, and the one-off guard caught ITC's Mar 2025 other income at 42% of PBT.
  **An eighth silent defect was found and fixed while wiring this up:** `_parse_growth_tables` read `find_previous(["p","h3"])` for each table's title, but Screener puts the title in the table's own first row (`<table class="ranges-table">`). It picked up the *previous* table's name and collapsed all four into one wrong entry — so `Compounded Sales Growth`, `Compounded Profit Growth` and `Return on Equity` have **always** read as unavailable, silently blocking checks 2 and 8 and the growth flags in `fundamental_agent`. Regression-tested against the real markup.
  Also fixed: check 4 scored the borrowings *trend* off any base, so a debt-free company going 2 Cr → 19 Cr read as "+863%" and got flagged — firing on exactly the companies the checklist should reward (observed live on ITC, D/E 0.03). Now requires D/E ≥ 0.10 before the trend is scored. Same low-base distortion the calculation guards already catch for profit CAGR.
  Narration goes through Ollama via the existing `LOCAL_MODEL_BASE_URL` client pattern, with the template summary as fallback so Stage 2 never blocks on a model call.

- [x] **`valuation_agent` — Stage 3, the DCF, all 10 steps** from the steps file including the parts usually skipped: the FCF-to-Net-Profit sanity check (Step 1), CAPM discount rate with **beta from more than one source** (Step 3), **both** terminal methods side by side — Gordon growth *and* an exit multiple (Step 5), the **reverse DCF** solving for the FCF the market price implies (Step 9), and the **sensitivity grid** (Step 10).
  Sensitivity is not optional garnish here — measured on a representative case, terminal value was **56% of total** intrinsic value, and assumption changes dominate data error: discount rate 11%→13% moved value **−22.7%**, terminal growth 3%→4.5% **+14.0%**, stage-1 growth 15%→18% **+12.6%**, while a 10% data error moved it 10%. A single point estimate would be false precision.
  Band per Step 8: `upper = intrinsic × 1.10`, `lower = intrinsic × 0.90`, `mos_buy_price = lower × 0.70`.
  ✅ **Built** in `app/agents/valuation_agent.py` + `app/data/market_risk.py`, 22 tests. Live results:

  | | RELIANCE | ITC |
  |---|---|---|
  | Base FCF (3yr avg) | ₹44,105 Cr | ₹15,193 Cr |
  | Beta (2y weekly / 5y monthly) | 1.10 / 0.98 | 0.56 / 0.87 |
  | Discount rate (CAPM) | 11.46% | 10.41% |
  | Net debt | ₹46,451 Cr | **−₹37,865 Cr (net cash)** |
  | Intrinsic / band | ₹792 (₹713–871) | ₹391 (₹352–430) |
  | MoS buy price | ₹499 | ₹246 |
  | Market price | ₹1,311 → **OVERVALUED** | ₹267 → **UNDERVALUED** |
  | Terminal share of value | 56% | 61% |

  **Beta is computed, not sourced** — no free source publishes one with a stated window, and the
  price history is already free through yfinance. Two windows (2y weekly, 5y monthly) against
  `^NSEI`; the **spread between them is the finding** the method asks for, not noise to average
  away. The **higher** estimate is used, which is the conservative direction: higher beta → higher
  discount rate → lower value. Cached in Redis for a week.

  **Risk-free rate is a reviewed config constant**, not a scrape (`RISK_FREE_RATE_PCT`, with
  `RISK_FREE_RATE_REVIEWED` beside it). A rate a month stale moves intrinsic value far less than
  the discount-rate choice already does; a scraper would add a fragile dependency for no accuracy.

  **The test that matters most** is `test_reverse_dcf_at_intrinsic_price_returns_the_base_fcf`:
  when the market price equals the intrinsic value, the FCF the price implies must equal the base
  FCF the model started from. That one invariant inverts the projection, terminal value,
  discounting and net-debt adjustment together, so it catches a sign error or an off-by-one year
  anywhere in the chain — which eyeballing a plausible-looking intrinsic value never would.
  A second test pins that **net cash ADDS to value** (ITC carries ~₹38,000 Cr of it).

  Refuses rather than guesses on: negative base FCF, terminal growth ≥ discount rate (Gordon
  diverges), banks/NBFCs, fewer than 3 years of FCF, and missing share count.

  **DCF inputs — all verified present on 2026-08-19, so this is unblocked except for two:**

  | Input | Source | Status |
  |---|---|---|
  | FCF series | `Free Cash Flow` row, cash flow | ✅ direct, no capex derivation needed |
  | Cash & equivalents | JSON schedules endpoint (4A) | ✅ 12 periods |
  | Current investments | `Investments` row, balance sheet | ✅ |
  | Total debt | `Borrowings` row, balance sheet | ✅ |
  | Shares outstanding | derived, Equity Capital ÷ Face Value (4A) | ✅ 13.53bn for RELIANCE |
  | Current market price | `Current Price` in top ratios | ✅ ₹1,313 |
  | TTM net profit / revenue (Step 9 reverse DCF) | `TTM` column, annual P&L | ✅ ₹88,167 Cr / ₹11,23,055 Cr |
  | **Beta** | **not published anywhere free** | ⚠️ compute from yfinance vs `^NSEI` |
  | **Risk-free rate** | **not in the codebase** | ⚠️ config setting |

  **Beta:** compute it rather than source it — `RELIANCE.NS` against `^NSEI` (the repo already
  uses yfinance with the `.NS` suffix, see `data/feed.py::_to_yfinance_symbol`). Run **two
  windows** (2yr weekly, 5yr monthly): the method asks for more than one source, and the spread
  between windows is itself the warning it wants about unstable betas.

  **Risk-free rate:** `RISK_FREE_RATE_PCT` in `config.py`, reviewed quarterly, with the review
  date stored next to it. The 10-year G-sec moves slowly; scraping it adds a fragile dependency
  for no accuracy gain, and a stale-by-a-month risk-free rate changes intrinsic value far less
  than the discount-rate choice already does.

  **Equity risk premium:** `EQUITY_RISK_PREMIUM_PCT`, default 4.5 (the method says 4-4.5% for India).

- [x] **`thesis_agent` — assemble, and store three things separately.** They have different lifetimes and costs, and fusing them means re-running the whole pipeline every time the price moves:

  | | Changes | Cost to recompute |
  |---|---|---|
  | `QualityVerdict` | quarterly, on results | full pipeline — scrape, 18 questions, 10-point checklist |
  | `ValuationBand` | quarterly, on assumptions | DCF run |
  | `Stance` | every tick | one division |

  ```python
  class QualityVerdict(BaseModel):    # stored, as_of quarter
      grade: str          # INVESTMENT_GRADE | WATCHLIST | NOT_INVESTABLE | NOT_RATED
      completeness: float # fraction of the 10 checks actually computed
      conviction: float   # how clear-cut the case is, given what we do have
      red_flags: list[dict]; scorecard: dict; as_of: date

  class ValuationBand(BaseModel):     # stored, as_of quarter
      intrinsic, upper, lower, mos_buy_price: float
      assumptions: dict; reverse_dcf_implied_fcf: float; sensitivity: dict; as_of: date

  class Stance(BaseModel):            # computed on read, never stored as truth
      action: str         # BUY | ADD | HOLD | WATCH | EXIT | AVOID | NOT_RATED
      price_vs_band: str; trigger_price: Optional[float]
      rule_applied: str   # the exact matrix cell — the verb must always be invertible
      computed_at: datetime
  ```
  Keep `conviction` and `completeness` distinct: "the data is complete and the case is marginal" is a different state from "the case looks strong but four inputs are missing."

- [x] **The stance matrix — note the asymmetry, it is the investment philosophy encoded.** The naive rule `price > upper band → SELL` is wrong for long-term investing and would churn out of exactly the compounders worth holding. Both the Varsity notes and professional practice agree: **sell on thesis break, not on price appreciation.** Expensive is a reason to stop buying, never a reason to exit a quality business.

  | | Quality PASS, not owned | Quality PASS, owned | Quality FAIL |
  |---|---|---|---|
  | Price < MoS buy price | **BUY** | **ADD** | AVOID / EXIT |
  | Price inside band | **WATCH @ ₹X** | **HOLD** | AVOID / EXIT |
  | Price above band | **WATCH @ ₹X** | **HOLD** *(never SELL)* | AVOID / EXIT |

  Quality failure overrides price in every row; price never overrides quality in any row. The "owned" column requires `investing_holdings` from 4A.

- [x] **`NOT_RATED` is a first-class outcome, and completeness gates the verb.** Real research desks carry Not Rated / Under Review / Rating Suspended. A system that emits BUY off 6 of 10 computed checks is worse than one that emits nothing. Force `NOT_RATED` when: completeness is below threshold, **any** Tier-1 Stage 1 red flag is present regardless of completeness, or the symbol is a bank/NBFC.

- [x] **Prose stays non-prescriptive; the verb lives in a structured field.** `fundamental_analysis_steps.md` ends with "don't issue a buy/sell recommendation" — the right convention for narrative text, and especially for LLM-generated text where a confident verb launders away uncertainty. The LLM narrates numbers already computed; it does not independently judge the company. The `Stance.action` verb is derived mechanically from the matrix above and always carries `rule_applied`, so it is a compression of the analysis rather than an opinion layered on top. Horizon is multi-year, reviewed quarterly.

---

### 4D — Surfaces

> ✅ **Backend surfaces built and verified live** — 7 routes under `/api/v1/investing`, the stance
> matrix in `app/services/stance.py` (27 tests), and `FundamentalScorecard` reshaped to store what
> the new design actually produces. ✅ **The frontend thesis view is now built too — 4D is closed.**
>
> **Proof the matrix works, from a live API call on ITC** (quality WATCHLIST, intrinsic ₹391,
> band ₹352–430, MoS ₹246.40, market ₹267):
>
> | | stance |
> |---|---|
> | holding 100 shares | **HOLD** |
> | same numbers, not held | **WATCH** |
>
> The price is *under* the band but *above* the margin-of-safety trigger, so it is not a buy either
> way — and ownership is the only thing separating the two answers. That is why the holdings
> register is not optional.
>
> ⚠️ **`fundamental_scorecards` was dropped and recreated.** The original columns
> (`verdict`/`confidence`/`profitability`/`dupont`/...) belonged to the earlier scorecard design and
> would have had to be misused to store quality + band + the three stage reports. The table was
> empty, so it was rebuilt rather than lived with. `create_all()` does not ALTER, so any future
> column change needs the same treatment or a real migration. The CASCADE also dropped
> `investing_holdings`' foreign key — it was recreated, and both FKs are verified present.

- [x] **`GET /api/v1/investing/{symbol}/thesis`** (viewer+) — latest stored `QualityVerdict` + `ValuationBand`, with `Stance` computed live against the current price. Generates on demand if nothing is cached within `FUNDAMENTALS_CACHE_SECONDS`.
- [x] **`POST /api/v1/investing/{symbol}/manual-input`** (trader+) — write to `fundamental_manual_inputs`. **A separate endpoint from `resume_workflow`, deliberately** (see 4B). Role is trader, not risk_manager — no money is at stake.
- [x] **`POST /api/v1/investing/holdings`** (trader+) — CRUD on the manual holdings register.
- [x] **`INVESTING_WATCHLIST_SYMBOLS`** in `config.py`, separate from `WATCHLIST_SYMBOLS`: `RELIANCE, TCS, INFY, HINDUNILVR, ITC, LT, ASIANPAINT, MARUTI, SUNPHARMA` (the three financials removed per 4A).
- [x] **Fixed a deadlock that hung the scraper on any standalone-only company.**
  Reported as `POST /investing/KPITECH/analyze` failing in the browser; the request never returned
  at all. The log stopped dead after "No consolidated page for KPITECH, falling back to standalone".

  `_fetch_page` held the module-level `asyncio.Lock` **while recursing into itself** for the 404
  fallback. `asyncio.Lock` is not reentrant, so the inner call waited forever on a lock its own
  caller held — no exception, no timeout, just a coroutine that stopped. It could only fire for a
  company with no consolidated page, so every symbol tested before this one missed it.

  Fixed by extracting `_get()`, whose critical section is exactly one request wide; the fallback now
  recurses with the lock free. A test asserts the lock is unheld inside each request, so the class of
  bug is prevented rather than the instance patched. The regression test runs under `wait_for`,
  because a re-broken version does not fail — it hangs.

  **Two things fell out of it:**
  - `_fetch_page` now returns `(html, was_consolidated)`. Asking for the consolidated view and
    silently getting the standalone one made every downstream figure claim to be consolidated when
    it was not — materially different statements for a company with subsidiaries.
  - Unknown tickers raise `SymbolNotFound` and the analyze route returns **404 with the actual
    reason** instead of running all three stages against no data and persisting a NOT_RATED
    scorecard for a company it never read. (KPITECH does not exist on Screener; KPIT Technologies is
    **KPITTECH**.)
  **Done when:** a standalone-only symbol completes and a bad one 404s. ✅ verified live: KPITTECH
  scrapes in 5s.

- [x] **Closed the last three `missing_data` items** — two were a wiring gap, not a data gap.

  **Checks 9 and 10 were already answered, by Stage 1, in the same run.** Check 9 read "Segment
  breakdown is in the annual report — not published in Screener's tables" while Q12 of the very same
  ASIANPAINT analysis said "Decorative 86.8%, International 9.4%, Industrial 3.8%". Same for check 10
  and Q18. Stage 2 could not see it because Stages 1-3 are parallel siblings.

  Fixed at the fan-in: `thesis_agent._reconcile_stage1_checks()` fills them from Q12/Q18, tagging the
  source as `stage1:q12:web` so the provenance survives the fold. Costs nothing — the evidence was
  already gathered. Writing `financial_report` from `thesis_agent` is safe because it runs AFTER the
  fan-in; that is sequential, not the concurrent write that raises `InvalidUpdateError`.

  They are recorded as **FLAG, never PASS**. Stage 1's answer is prose, and turning "three segments,
  86.8/9.4/3.8" into a pass/fail is a judgement this node has no basis to make — so they count toward
  completeness and stay out of the pass tally, which is exactly what they are: answered, not scored.
  If Stage 1 could not answer either, `thesis_agent` appends the MissingDatum instead, so nothing is
  silently dropped.

  **Gross margin had a structured source nobody had looked for.** Screener's HTML carries only total
  Expenses and OPM%, which is why check 1 reported NOT_COMPUTABLE with operating margin as a labelled
  proxy. But the JSON schedules endpoint — already used for cash — breaks that same Expenses row into
  `Material Cost %` / `Manufacturing Cost %` / `Employee Cost %` / `Other Cost %`, **twelve years of
  it**. Gross margin is 100 minus material cost; percentages of sales also sidestep reconciling
  absolute figures between consolidated and standalone statements.

  Per the method's own general rule (never silently pick one assumption when more than one is
  reasonable), both definitions are reported: materials-only is primary and carries the bar,
  materials-plus-conversion is stated beside it. ASIANPAINT: **50.7% primary, 42.6% strict, +9% over
  5 years, PASS against a 20% bar** — where it previously showed a rejected 19% proxy.

  **Result on ASIANPAINT: Stage 2 completeness 70% → 100%, missing_data 3 → 0.**
  **Done when:** the three items disappear without being silently dropped. ✅ 345 passing.

- [x] **Web search as a Stage 1 source.** Filings answer first; web search fills what they leave
  empty. `app/data/web_search.py` (OpenAI Responses API `web_search` tool), wired into
  `business_agent._answer` at three points: as the PRIMARY route for `Kind.EXTERNAL` (Q2 — no
  annual report discloses its own promoter's regulatory history), and as a fallback when retrieval
  returns nothing or drafts a non-answer.

  **What prompted it:** ASIANPAINT scored 1/18 with every answer reading "no relevant passage
  found" — which reads as "unknowable" rather than "nothing was ever downloaded". Only RELIANCE had
  ever been ingested. After ingesting ASIANPAINT (6 documents, 3,413 chunks) *and* adding search,
  the same symbol scores **18/18 in 20s — 8 from filings, 10 from web**.

  **The tool call must be FORCED, and this is the whole point.** `tools=[{"type": "web_search"}]`
  only OFFERS it; measured against the live API the model frequently declines and answers from
  parametric memory — fluent, plausible, zero citations. A response with no `web_search_call` item
  is discarded rather than returned, because it was recollection, not research.

  Every answer records `source` (DOCUMENTS | WEB | SHAREHOLDING | NONE) and web answers carry
  `{title, url, domain}`, rendered as links with the domain leading so a regulator's page can be
  weighed differently from a content farm. A `_SearchBudget` caps paid searches per run.

  **Two defects found while building it, both fixed:**
  - The limiting semaphore was a module-level singleton. `asyncio.Semaphore` binds to the loop that
    first awaits it, so it worked in the server (one long-lived loop) and raised "bound to a
    different event loop" in anything calling `asyncio.run` twice. Now keyed by loop.
  - `_synthesise` returned the raw passages on FAILURE, identically to how it returns them when
    deliberately DISABLED. Observed live with an exhausted API key: a clean 18/18 whose answers were
    passage dumps. Completeness gates the verdict, so an outage was buying a grade. Failure now
    raises `SynthesisUnavailable` and the answer is marked PARTIAL.

  **Tests must never reach the live API.** They were doing so silently — an autouse fixture now
  disables search module-wide, and the fallback tests mock `search_answer`.
  **Done when:** the tier split is visible in the UI and tests cover precedence. ✅ 330 passing.

  **Known limitation, measured:** web answers are non-deterministic and can be confidently wrong.
  Two runs of Q2 on ASIANPAINT gave a correct answer (Dani/Choksi/Vakil families, Choksey exited in
  the late 1990s) and an incorrect one (naming four current promoter-directors as the 1942 founders)
  — same sources, different synthesis. This is why the tier badge exists and why nothing sourced
  from the web may ever produce a BLOCK.

- [x] **Frontend thesis view** — ~~fine to stub with raw JSON initially~~; built properly instead.
  `/dashboard/investing` lists the watchlist and holdings register; `/dashboard/investing/[symbol]`
  is the thesis view. Components in `frontend/src/components/investing/`:

  - `StanceHeader` — the verb, the market price, the trigger price, and `rule_applied` beneath it.
    The action and the rule are **one component with no prop that separates them**: a verb without
    its rule is an oracle, and the backend computes `rule_applied` on every branch precisely so the
    answer stays invertible. Ownership sits on the header too, since it is an input to the verb.
  - `ValuationBand` — the band as the primary object, with intrinsic value as a tick inside it
    rather than a headline number, the margin-of-safety zone shaded, and a marker for where the
    market actually is. Assumptions, the CAPM line (with the risk-free review date), the reverse-DCF
    line, and the full sensitivity grid, which is shown by default rather than hidden behind a
    toggle — the reader should see how much of the answer is assumption before they see the answer.
  - `QualityScorecard` — grade with completeness *and* conviction beside it, both as separate meters.
    Red flags listed apart from routine findings; calculation cautions (checks numbered 100+)
    separated so they cannot dilute the pass/fail count.
  - `BusinessChecklist` — the 18 questions with citations, unanswered ones shown rather than
    filtered, judgement-derived answers labelled as such, and the gate worded as "nothing found"
    rather than "clean".
  - `MissingDataPanel` — every `MissingDatum` with a "provide this" form pre-filled with the field
    and period. **The source note is mandatory in the form though the API accepts null**: a
    hand-entered value is the highest-trust tier and outranks the scraper, so it has to be
    attributable. Submitting stores the value and offers a re-run; it deliberately does not resume
    a workflow.
  - `HoldingControl` — records a position bought by hand, and surfaces the trading-watchlist overlap
    warning the backend returns.

  **Two things the live payload forced a change to.** Each Stage 2 check's `value` is a *different
  unitless quantity* (a growth gap on one, debt/equity on another) and is populated even on
  `NOT_COMPUTABLE` checks — ITC's gross-margin check carries `35.0`, an operating-margin proxy the
  check explicitly rejected. Rendering it bare showed a rejected proxy as a result, so the column
  shows the status and the numbers stay in `detail` where they carry units. Separately, "N of 10
  passed" merged failures with figures that could not be computed, so the counts are listed apart.

  **Not a trade ticket:** no quantity field, no order button, no path to the broker. The only
  actions are re-run, record a holding, and supply a missing figure.
  **Done when:** `npm run build` succeeds and the payload types match the API. ✅ builds clean;
  types verified against a live `FundamentalScorecard` row rather than written from the schema.

---

### 4E — Monitoring and separation

- [ ] **Quarterly re-review job** (reuse the `apscheduler` pattern in `jobs/scheduler.py`): re-run the pipeline per symbol in `investing_holdings` + `INVESTING_WATCHLIST_SYMBOLS`, and alert via Telegram when a `QualityVerdict.grade` **degrades** or a new Tier-1 red flag appears. This is the highest-value part of the feature — you need to know when to sell something you bought by hand — and it is why the holdings register exists.

- [ ] **Track the calls.** Store the `Stance` at each quarterly review and score it later. The repo already does win-rate and calibration for trading; research desks do the same for ratings, and it is the only way to discover whether the DCF assumptions run systematically optimistic.

- [ ] **Trading/investing separation — most of it dissolves, two items survive.** Because Investing mode never writes a `Trade` row, `exit_monitor`'s stop-loss sweep, the 3:00 PM force-exit, the daily-loss-cap kill-switch trip, and `position_reconciler` all have nothing to trip over, and `Trade.analysis_mode` is not needed. Two real conflicts remain, both created by *your manual* buys rather than by the system:
  - **Same-symbol hazard.** You hold RELIANCE in demat; the trading side shorts RELIANCE intraday as MIS; Zerodha may treat that as a delivery sell. Add a **disjoint-watchlist validation** at startup — `WATCHLIST_SYMBOLS ∩ INVESTING_WATCHLIST_SYMBOLS` must be empty — and a warning when a symbol in `investing_holdings` is proposed for a trading short.
  - **Invisible margin consumption.** A manual delivery buy consumes real cash and reduces intraday margin, and the system cannot see why. Surface it as an informational note on the portfolio panel, not as a control.

- [ ] **News/events upgrade (parallelisable):** fix `news_fetcher.py` so `fetch_news(symbol=...)` filters articles by company-name match rather than only namespacing the cache key — currently every symbol receives the same generic feed. If the MarketPulse NSE/BSE corporate-announcement scraper is far enough along, reuse its output as a `corporate_events` feed here instead of building a second scraper.

---

## Order of operations summary

Phase 0 → Phase 1 → Phase 2 → Phase 3 can mostly happen in parallel with each other once Phase 0 is done (they touch different files). Phase 4 depends on nothing above except general codebase stability — the earlier caveat about Phase 2's HITL race condition no longer applies, because Investing mode places no orders and so has no HITL execution path of its own.

Within Phase 4 the order is load-bearing: **4A → 4B → 4C → 4D/4E**. 4A first because every later stage reads from it and because the six defects listed there produce silently wrong numbers rather than errors — building on them means debugging a verdict instead of a parser. 4D and 4E can run in parallel once 4C lands.
