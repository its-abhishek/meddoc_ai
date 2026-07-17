from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

_env_file = str(Path(__file__).resolve().parent.parent / ".env")


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    DATABASE_URL_SYNC: str = ""
    REDIS_URL: str = "redis://localhost:6379/0"
    GROQ_API_KEY: str = ""
    GROQ_REASONING_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_CLASSIFICATION_MODEL: str = "llama-3.1-8b-instant"
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    STORAGE_PATH: str = "./storage"
    MAX_UPLOAD_SIZE_MB: int = 20

    class Config:
        env_file = _env_file

    def get_database_url(self) -> str:
        url = self.DATABASE_URL
        if url and not url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        return url

    def get_database_url_sync(self) -> str:
        url = self.DATABASE_URL_SYNC
        if url and url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgresql://")
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
