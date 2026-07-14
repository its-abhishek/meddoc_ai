"""Event emission via Redis Pub/Sub for agent lifecycle events."""
import json
import logging
import time
import redis
from config import get_settings

logger = logging.getLogger(__name__)

_redis_client = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def emit_event(
    tenant_id: str,
    document_id: str,
    node: str,
    status: str,
    detail: str = "",
    extra: dict = None,
):
    """Publish an agent lifecycle event to Redis Pub/Sub."""
    r = get_redis()
    channel = f"agent-events:{tenant_id}"
    event = {
        "tenant_id": tenant_id,
        "document_id": document_id,
        "node": node,
        "status": status,
        "detail": detail,
        "timestamp": time.time(),
    }
    if extra:
        event.update(extra)
    try:
        r.publish(channel, json.dumps(event))
        logger.debug(f"Emitted event: {channel} -> {node}:{status}")
    except Exception as e:
        logger.error(f"Failed to emit event: {e}")


def emit_event_with_db(
    tenant_id: str,
    document_id: str,
    node: str,
    status: str,
    detail: str = "",
    extra: dict = None,
):
    """Emit event and also persist to monitoring_events table (async)."""
    emit_event(tenant_id, document_id, node, status, detail, extra)
    # DB persistence is handled by the monitoring service subscriber
