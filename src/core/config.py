"""pydantic configuration for .env"""
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """config"""
    OPENAI_API_KEY: str
    GEMINI_API_KEY: str
    MILLIS_API_KEY: str  # cSpell:disable-line
    OPENAI_MODEL_NAME: str

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

Config = Settings()
