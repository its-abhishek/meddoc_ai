from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:9743816791@localhost:5433/meddocs"
    DATABASE_URL_SYNC: str = "postgresql://postgres:9743816791@localhost:5433/meddocs"
    REDIS_URL: str = "redis://localhost:6379/0"
    GROQ_API_KEY: str = "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"
    GROQ_REASONING_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_CLASSIFICATION_MODEL: str = "llama-3.1-8b-instant"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    STORAGE_PATH: str = "./storage"
    MAX_UPLOAD_SIZE_MB: int = 20

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
