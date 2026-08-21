from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # --------------------------------------------------------
    # POSTGRESQL
    # --------------------------------------------------------
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str

    # --------------------------------------------------------
    # REDIS
    # --------------------------------------------------------
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int

    # --------------------------------------------------------
    # QDRANT (vector DB for episodic memory)
    # --------------------------------------------------------
    QDRANT_HOST: str
    QDRANT_PORT: int
    QDRANT_COLLECTION: str = "trade_memories"

    # --------------------------------------------------------
    # LANGSMITH TRACING
    # --------------------------------------------------------
    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "futureedge"

    # --------------------------------------------------------
    # ZERODHA BROKER
    # --------------------------------------------------------
    ZERODHA_API_KEY: str = ""
    ZERODHA_API_SECRET: str = ""
    ZERODHA_ACCESS_TOKEN: str = ""

    # --------------------------------------------------------
    # ACTIVE BROKER & FEED
    # --------------------------------------------------------
    ACTIVE_BROKER: str = "mock"
    ACTIVE_FEED: str = "mock"

    # --------------------------------------------------------
    # DEFAULT INSTRUMENT
    # --------------------------------------------------------
    DEFAULT_SYMBOL: str = "NIFTY 50"
    DEFAULT_INSTRUMENT_TOKEN: int = 256265

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    FRONTEND_URL: str = "http://localhost:3000"
    ENCRYPTION_KEY: str = ""

    # Comma-separated IPs of reverse proxies allowed to set X-Forwarded-For.
    # Empty (the default) means "no proxy" — rate limiting keys on the direct
    # socket peer and X-Forwarded-For is ignored entirely, so a client cannot
    # spoof its way around a limit by sending the header itself. Set this to
    # your nginx/Caddy/Traefik IP when deploying behind one, and pass the same
    # value to uvicorn's --forwarded-allow-ips.
    TRUSTED_PROXY_IPS: str = ""

    # --------------------------------------------------------
    # NOTIFICATIONS (Twilio/WhatsApp — disabled, everything on dashboard)
    # --------------------------------------------------------
    WHATSAPP_ALERTS_ENABLED: bool = False
    ADMIN_WHATSAPP_NUMBER: str = ""
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = ""
    NOTIFY_PHONE_NUMBER: str = ""

    # --------------------------------------------------------
    # TELEGRAM BOT CONFIG
    # --------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --------------------------------------------------------
    # JWT AUTHENTICATION
    # --------------------------------------------------------
    JWT_SECRET_KEY: str = ""
    JWT_ACCESS_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 7

    # --------------------------------------------------------
    # AI MODELS
    #
    # OPENAI_API_KEY:
    #   Used by llm_reasoner for trade rationale generation.
    #   If empty, falls back to template-based rationale.
    #
    # LLM_REASONING_ENABLED:
    #   true  → orchestrator calls LLM to explain the consensus
    #   false → skip LLM call, use template only
    #
    # FINBERT_ENABLED:
    #   true  → use ProsusAI/finbert for news sentiment
    #   false → use keyword matching (fast, no download)
    # --------------------------------------------------------
    OPENAI_API_KEY: str = ""
    FINBERT_ENABLED: bool = False
    LLM_REASONING_ENABLED: bool = True
    LOCAL_MODEL_API_KEY: str = ""
    LOCAL_MODEL_BASE_URL: str = ""
    LOCAL_MODEL_NAME: str = "gpt-4o-mini"

    # --------------------------------------------------------
    # ADAPTIVE AGENT WEIGHTS
    #
    # Every WEIGHT_UPDATE_INTERVAL_TRADES closed trades,
    # we recalculate each agent's accuracy and update weights.
    # --------------------------------------------------------
    WEIGHT_UPDATE_INTERVAL_TRADES: int = 20
    MIN_TRADES_FOR_WEIGHT_UPDATE: int = 10

    # --------------------------------------------------------
    # EXIT MONITORING & STRATEGY
    #
    # EXIT_MONITOR_INTERVAL_SECONDS:
    #   How often the exit monitor checks open trades against
    #   current prices. Lower = more responsive but more load.
    #
    # MAX_DAILY_LOSS_PCT:
    #   Auto-activate kill switch if cumulative daily loss
    #   exceeds this percentage of total equity.
    # --------------------------------------------------------
    EXIT_MONITOR_INTERVAL_SECONDS: int = 10
    MAX_DAILY_LOSS_PCT: float = 3.0
    POSITION_RECONCILE_INTERVAL_SECONDS: int = 300
    RECONCILE_AUTO_HALT: bool = True
    
    # Trailing Stop Config
    TRAILING_STOP_ENABLED: bool = True
    TRAILING_STOP_TRIGGER_PCT: float = 2.0
    TRAILING_STOP_DISTANCE_PCT: float = 1.5

    # Position Pyramiding / Scaling-in limits
    MAX_PYRAMID_ENTRIES: int = 3
    MAX_SYMBOL_EXPOSURE: float = 50000.0

    # --------------------------------------------------------
    # HITL SMART GATING (Risk-score-based auto-approval)
    #
    # HITL_AUTO_APPROVE_ENABLED:
    #   true  → Only trigger HITL for HIGH RISK trades (recommended)
    #   false → HITL on every trade (old behaviour, kills intraday)
    #
    # HITL_RISK_SCORE_THRESHOLD:
    #   HITL required if risk_score > this value (0.0–1.0)
    #
    # HITL_CONFIDENCE_THRESHOLD:
    #   HITL required if confidence < this value
    #
    # HITL_POSITION_SIZE_THRESHOLD_PCT:
    #   HITL required if position > X% of equity
    #
    # HITL_VIX_THRESHOLD:
    #   HITL required if India VIX > this value
    # --------------------------------------------------------
    HITL_AUTO_APPROVE_ENABLED: bool = True
    HITL_RISK_SCORE_THRESHOLD: float = 0.6
    HITL_CONFIDENCE_THRESHOLD: float = 0.65
    HITL_POSITION_SIZE_THRESHOLD_PCT: float = 15.0
    HITL_VIX_THRESHOLD: float = 18.0

    # --------------------------------------------------------
    # VIX SPIKE DETECTION
    #
    # VIX_EXTREME_THRESHOLD: Absolute VIX level that always triggers VETO
    # VIX_SPIKE_PCT: % rise from 5-day avg that triggers VETO (e.g. 30%)
    # VIX_CAUTION_THRESHOLD: VIX above this = sell bias, not full VETO
    # --------------------------------------------------------
    VIX_EXTREME_THRESHOLD: float = 30.0
    VIX_SPIKE_PCT: float = 30.0
    VIX_CAUTION_THRESHOLD: float = 22.0

    # --------------------------------------------------------
    # DAILY P&L TELEGRAM REPORT
    # Sends an end-of-day summary via Telegram at 15:45 IST
    # --------------------------------------------------------
    DAILY_REPORT_ENABLED: bool = True
    DAILY_REPORT_HOUR_IST: int = 15
    DAILY_REPORT_MINUTE_IST: int = 45

    # Watchlist
    WATCHLIST_SYMBOLS: str = "NIFTY 50,BANKNIFTY"

    # Investing-mode watchlist — kept SEPARATE from the trading watchlist, and
    # required to be disjoint from it: you may hold a symbol in demat from a
    # manual buy while the trading side shorts it intraday as MIS, which the
    # broker can treat as a delivery sell.
    #
    # Banks and NBFCs are deliberately absent (HDFCBANK, ICICIBANK, BAJFINANCE
    # were in the original list). Screener serves lenders a different P&L
    # schema, and leverage/coverage ratios do not carry their usual meaning
    # when borrowing IS the raw material — those symbols return NOT_RATED.
    INVESTING_WATCHLIST_SYMBOLS: str = (
        "RELIANCE,TCS,INFY,HINDUNILVR,ITC,LT,ASIANPAINT,MARUTI,SUNPHARMA"
    )

    # --------------------------------------------------------
    # VALUATION (Investing mode, Stage 3 DCF)
    # --------------------------------------------------------
    # Discount rate is CAPM: risk-free + beta x equity risk premium.
    #
    # RISK_FREE_RATE_PCT is the 10-year Indian government bond yield. It is a
    # reviewed constant rather than a scrape on purpose: the 10-year G-sec
    # moves slowly, and a rate that is a month stale changes intrinsic value
    # far less than the discount-rate choice itself already does (a 11% -> 13%
    # move is worth about -23%). A scraper here would add a fragile dependency
    # for no accuracy. Update it quarterly and move the review date with it.
    RISK_FREE_RATE_PCT: float = 6.5
    RISK_FREE_RATE_REVIEWED: str = "2026-08-19"

    # The method puts India's equity risk premium at 4-4.5%.
    EQUITY_RISK_PREMIUM_PCT: float = 4.5

    # Two-stage FCF growth. Conservative by default — very few companies
    # sustain FCF growth above ~20% for long, so anything higher is an
    # aggressive scenario to be shown alongside the base case, not as it.
    DCF_STAGE1_GROWTH_PCT: float = 15.0   # years 1-5
    DCF_STAGE2_GROWTH_PCT: float = 10.0   # years 6-10
    DCF_TERMINAL_GROWTH_PCT: float = 3.5  # 3-4% at most, never >= discount rate
    DCF_EXIT_MULTIPLE: float = 20.0       # for the alternative terminal value

    # --------------------------------------------------------
    # DOCUMENT INGESTION (Investing mode, Stage 1)
    # --------------------------------------------------------
    # A SEPARATE Qdrant collection from QDRANT_COLLECTION. That one holds
    # 12-dimensional hand-built market vectors (memory/embedder.py); these are
    # text embeddings of a different dimension entirely. Same database, and
    # they must not share a collection.
    QDRANT_DOCUMENTS_COLLECTION: str = "company_documents"

    # Embeddings use OpenAI by default (OPENAI_API_KEY). If LOCAL_MODEL_BASE_URL
    # is set, that endpoint is used instead — the client is OpenAI-compatible
    # either way, so switching provider is configuration, not code.
    #
    # Cost is not the constraint here: the full watchlist is roughly 5.5M tokens
    # to embed once (a 187-page annual report is ~285k), which is cents at
    # text-embedding-3-small rates. Re-ingesting a document already stored is a
    # no-op — chunk ids are deterministic — so this does not recur.
    #
    # CHANGE THE DIMENSION WITH THE MODEL. Qdrant fixes vector size at
    # collection creation, so a mismatch fails every upsert against an existing
    # collection (drop and re-create it if you switch).
    #   text-embedding-3-small -> 1536   (default; best cost/quality here)
    #   text-embedding-3-large -> 3072
    #   nomic-embed-text       -> 768    (local, via LOCAL_MODEL_BASE_URL)
    EMBEDDING_MODEL_NAME: str = "text-embedding-3-small"
    EMBEDDING_DIMENSION: int = 1536

    # Chunk size is a retrieval tradeoff, not a storage one: too small and an
    # answer gets split across chunks, too large and the citation stops
    # pointing anywhere useful. Chunks never span pages, so a citation always
    # names one page.
    DOCUMENT_CHUNK_CHARS: int = 1200
    DOCUMENT_CHUNK_OVERLAP: int = 150

    # Stage 1 synthesis: an LLM drafts each answer from RETRIEVED passages only.
    # Roughly 18 questions x ~2k input tokens per company, run quarterly — small,
    # because retrieval means a 190-page PDF is never sent to a model.
    # Turn synthesis off to get retrieval-only output: the passages and their
    # citations, with no drafted answer. Everything downstream still works;
    # you read three paragraphs instead of one sentence.
    STAGE1_SYNTHESIS_ENABLED: bool = True
    STAGE1_SYNTHESIS_MODEL: str = "gpt-4o-mini"
    # Retrieved passages per question, after merging several query phrasings.
    #
    # MEASURED, not guessed — and raising it made things WORSE. Going from 5 to
    # 8 (and keeping 2x that after the merge) dropped RELIANCE from 12/18 to
    # 10/18: Q5 and Q7 went from answered back to not-found. Feeding more
    # passages buys recall at the cost of precision, and a model handed mostly
    # irrelevant fragments declines to answer rather than digging the good one
    # out. Do not raise this without re-measuring the answered count.
    STAGE1_TOP_K: int = 5

    # Web search — the fallback when the company's own documents cannot answer.
    #
    # WHY A FALLBACK AND NOT A PARALLEL SOURCE: the annual report is audited and
    # the open web is not, so filings answer first and search only fills what
    # they leave empty. Each answer records which tier it came from.
    #
    # This is also what rescues a symbol whose report was never ingested. Those
    # used to score 1/18 with every answer reading "no relevant passage found",
    # which looks like the information does not exist rather than like nothing
    # was ever downloaded.
    #
    # COST: roughly $10 per 1,000 searches plus tokens. The worst case is a
    # company with no documents at all, which fires one search per unanswered
    # question — under 20 cents for a full 18-question run, quarterly.
    STAGE1_WEB_SEARCH_ENABLED: bool = True
    STAGE1_WEB_SEARCH_MODEL: str = "gpt-4o-mini"
    STAGE1_WEB_SEARCH_TIMEOUT_S: int = 120
    # Stage 1 fans out over all 18 questions at once; this bounds how many of
    # them may be searching simultaneously.
    STAGE1_WEB_SEARCH_CONCURRENCY: int = 6
    # Hard ceiling per analysis, so a company with an empty document store
    # cannot quietly turn one run into an unbounded number of paid searches.
    STAGE1_WEB_SEARCH_MAX_QUESTIONS: int = 18

    # --------------------------------------------------------
    # APSCHEDULER SETTINGS
    # --------------------------------------------------------
    SCHEDULER_MAX_INSTANCES: int = 1
    SCHEDULER_MISFIRE_GRACE_TIME: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()