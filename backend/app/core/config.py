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

    # Watchlist
    WATCHLIST_SYMBOLS: str = "NIFTY 50,BANKNIFTY"

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