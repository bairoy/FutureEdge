from pydantic_settings import BaseSettings,SettingsConfigDict 

class Settings(BaseSettings):
  OPENAI_API_KEY:str
  
  POSTGRES_HOST:str
  POSTGRES_PORT:int
  POSTGRES_DB:str
  POSTGRES_USER:str
  POSTGRES_PASSWORD:str 

  REDIS_HOST:str
  REDIS_PORT:int
  REDIS_DB:int 

  QDRANT_HOST:str
  QDRANT_PORT:int
  QDRANT_COLLECTION:str="trade_memories"

  LANGCHAIN_TRACING_V2:bool=True
  LANGCHAIN_API_KEY:str
  LANGCHAIN_PROJECT:str

  ZERODHA_API_KEY:str=""
  ZERODHA_API_SECRET:str="" 
  ZERODHA_ACCESS_TOKEN:str="" 

  ACTIVE_BROKER:str="mock"
  ACTIVE_FEED:str="mock"

  DEFAULT_SYMBOL:str="NIFTY 50"
  DEFAULT_INSTRUMENT_TOKEN:int = 256265

  APP_ENV:str = "development"
  LOG_LEVEL:str="INFO"

  JWT_SECRET_KEY:str=""
  JWT_ACCESS_EXPIRE_MINUTES:int
  JWT_REFRESH_EXPIRE_DAYS:int
    # --------------------------------------------------------
    # PHASE 2 — AI MODELS
    #
    # ANTHROPIC_API_KEY:
    #   Get from https://console.anthropic.com
    #   Used by the orchestrator LLM reasoning step.
    #   If empty, LLM reasoning is skipped (graceful fallback).
    #
    # FINBERT_ENABLED:
    #   True  → use ProsusAI/finbert model for sentiment analysis
    #            (requires transformers + torch, downloads ~400MB on first run)
    #   False → use keyword matching (fast, no download, good for testing)
    #
    # LLM_REASONING_ENABLED:
    #   True  → orchestrator calls Claude to explain the consensus
    #            and inject reasoning into the HITL review payload
    #   False → skip LLM call, use pure weighted math only
    # --------------------------------------------------------
    # ANTHROPIC_API_KEY:      str  = ""
  OPENAI_API_KEY:str=""
  FINBERT_ENABLED:bool = False
  LLM_REASONING_ENABLED:bool = True
# --------------------------------------------------------
    # PHASE 2 — ADAPTIVE AGENT WEIGHTS
    #
    # Every WEIGHT_UPDATE_INTERVAL_TRADES closed trades,
    # we recalculate each agent's accuracy and update weights.
    # MIN_TRADES_FOR_WEIGHT_UPDATE: need at least this many trades
    # before trusting per-agent accuracy numbers.
    # --------------------------------------------------------
  WEIGHT_UPDATE_INTERVAL_TRADES: int = 20
  MIN_TRADES_FOR_WEIGHT_UPDATE:  int = 10

  model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
  )
settings = Settings()