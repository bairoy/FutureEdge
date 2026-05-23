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



  model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
  )
settings = Settings()