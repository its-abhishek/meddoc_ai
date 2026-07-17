"""Database connection and session management."""
import ssl as _ssl
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from config import get_settings

settings = get_settings()

_engine = None
_async_session_factory = None
_sync_engine = None
_sync_session_factory = None


def _clean_url(url: str) -> str:
    """Strip sslmode param (asyncpg doesn't support it, uses ssl param instead)."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params.pop("sslmode", None)
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _get_async_engine():
    global _engine
    if _engine is None:
        url = _clean_url(settings.get_database_url())
        parsed = urlparse(url)
        is_remote = parsed.hostname not in ("localhost", "127.0.0.1", None)
        connect_args = {}
        if is_remote:
            connect_args["ssl"] = _ssl.create_default_context()
        _engine = create_async_engine(
            url, echo=False, pool_size=20, max_overflow=10,
            connect_args=connect_args,
        )
    return _engine


def _get_async_session_factory():
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(_get_async_engine(), class_=AsyncSession, expire_on_commit=False)
    return _async_session_factory


def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.get_database_url_sync(), echo=False, pool_size=5, max_overflow=5)
    return _sync_engine


def get_sync_session_factory():
    global _sync_session_factory
    if _sync_session_factory is None:
        _sync_session_factory = sessionmaker(bind=_get_sync_engine())
    return _sync_session_factory


class Base(DeclarativeBase):
    pass


async def get_db():
    async with _get_async_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(__import__('sqlalchemy').text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            __import__('sqlalchemy').text(
                "ALTER TABLE documents ADD COLUMN IF NOT EXISTS clinical_notes JSONB DEFAULT '[]'::jsonb"
            )
        )
