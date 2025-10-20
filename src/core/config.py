"""pydantic configuration for .env"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """config"""
    OPENAI_API_KEY: str
    GEMINI_API_KEY: str
    MILLIS_API_KEY: str  # cSpell:disable-line
    OPENAI_MODEL_NAME: str
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    ARQ_QUEUE_NAME: str = "default"
    ARQ_WORKER_CONCURRENCY: int = 4

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

Config = Settings()
