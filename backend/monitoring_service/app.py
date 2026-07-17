"""Monitoring microservice — separate FastAPI app for live pipeline tracking with SSE streaming."""
import asyncio
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Set
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
import redis.asyncio as aioredis
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="MedDocs Monitoring Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

sse_subscribers: Dict[str, Set[asyncio.Queue]] = {}


async def subscribe_to_redis():
    pubsub = redis_client.pubsub()
    await pubsub.psubscribe("agent-events:*")

    async for message in pubsub.listen():
        if message["type"] != "pmessage":
            continue
        try:
            event = json.loads(message["data"])
            doc_id = event.get("document_id")
            tenant_id = event.get("tenant_id")

            await _persist_event(event)

            if doc_id and doc_id in sse_subscribers:
                for queue in sse_subscribers[doc_id]:
                    await queue.put(event)

            if event.get("status") in ("completed", "failed"):
                await _send_notification(event)

        except Exception as e:
            logger.error(f"Error processing event: {e}")


async def _persist_event(event: dict):
    async with async_session() as db:
        try:
            await db.execute(
                text("""
                    INSERT INTO monitoring_events (id, tenant_id, document_id, node, status, detail, timestamp)
                    VALUES (:id, :tenant_id, :document_id, :node, :status, :detail, NOW())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": event["tenant_id"],
                    "document_id": event["document_id"],
                    "node": event["node"],
                    "status": event["status"],
                    "detail": event.get("detail", ""),
                },
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist event: {e}")


async def _send_notification(event: dict):
    async with async_session() as db:
        try:
            message = f"Document processing {event['status']}: {event.get('detail', '')}"
            await db.execute(
                text("""
                    INSERT INTO notifications (id, tenant_id, document_id, message, read, created_at)
                    VALUES (:id, :tenant_id, :document_id, :message, false, NOW())
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": event["tenant_id"],
                    "document_id": event["document_id"],
                    "message": message,
                },
            )
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")


@app.on_event("startup")
async def startup():
    asyncio.create_task(subscribe_to_redis())


@app.get("/monitor/documents/{document_id}/stream")
async def stream_document_events(document_id: str):
    queue: asyncio.Queue = asyncio.Queue()
    if document_id not in sse_subscribers:
        sse_subscribers[document_id] = set()
    sse_subscribers[document_id].add(queue)

    async def event_generator():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield {"event": "agent_event", "data": json.dumps(event)}
                if event.get("status") in ("completed", "failed") and event.get("node") == "pipeline":
                    break
        except asyncio.TimeoutError:
            yield {"event": "heartbeat", "data": "{}"}
        finally:
            sse_subscribers[document_id].discard(queue)
            if not sse_subscribers[document_id]:
                del sse_subscribers[document_id]

    return EventSourceResponse(event_generator())


@app.get("/monitor/documents/{document_id}/status")
async def get_document_status(document_id: str):
    async with async_session() as db:
        result = await db.execute(
            text("""
                SELECT node, status, detail, timestamp
                FROM monitoring_events
                WHERE document_id = :document_id
                ORDER BY timestamp ASC
            """),
            {"document_id": document_id},
        )
        events = result.fetchall()

    if not events:
        raise HTTPException(404, "No events found for this document")

    current_node = events[-1][0]
    current_status = events[-1][1]

    return {
        "document_id": document_id,
        "current_node": current_node,
        "current_status": current_status,
        "events": [
            {"node": e[0], "status": e[1], "detail": e[2], "timestamp": str(e[3])}
            for e in events
        ],
    }


@app.get("/monitor/tenants/{tenant_id}/active")
async def get_active_documents(tenant_id: str):
    async with async_session() as db:
        result = await db.execute(
            text("""
                WITH latest_events AS (
                    SELECT document_id, node, status, timestamp,
                           ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY timestamp DESC) as rn
                    FROM monitoring_events
                    WHERE tenant_id = :tenant_id
                )
                SELECT document_id, node, status, timestamp
                FROM latest_events
                WHERE rn = 1 AND status NOT IN ('completed', 'failed')
            """),
            {"tenant_id": tenant_id},
        )
        active = result.fetchall()

    return [
        {
            "document_id": str(a[0]),
            "current_node": a[1],
            "current_status": a[2],
            "last_event": str(a[3]),
        }
        for a in active
    ]


@app.get("/monitor/tenants/{tenant_id}/notifications")
async def get_notifications(tenant_id: str, unread_only: bool = True):
    async with async_session() as db:
        query = "SELECT id, document_id, message, read, created_at FROM notifications WHERE tenant_id = :tenant_id"
        if unread_only:
            query += " AND read = false"
        query += " ORDER BY created_at DESC LIMIT 50"

        result = await db.execute(text(query), {"tenant_id": tenant_id})
        notifs = result.fetchall()

    return [
        {
            "id": str(n[0]),
            "document_id": str(n[1]),
            "message": n[2],
            "read": n[3],
            "created_at": str(n[4]),
        }
        for n in notifs
    ]


@app.get("/monitor/tenants/{tenant_id}/all-documents")
async def get_all_documents(tenant_id: str):
    async with async_session() as db:
        result = await db.execute(
            text("""
                WITH latest_events AS (
                    SELECT document_id, node, status, detail, timestamp,
                           ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY timestamp DESC) as rn
                    FROM monitoring_events
                    WHERE tenant_id = :tenant_id
                )
                SELECT d.id, d.source_filename, d.upload_status,
                       le.node, le.status, le.detail, le.timestamp
                FROM documents d
                LEFT JOIN latest_events le ON d.id = le.document_id AND le.rn = 1
                WHERE d.tenant_id = :tenant_id
                ORDER BY COALESCE(le.timestamp, d.created_at) DESC
                LIMIT 50
            """),
            {"tenant_id": tenant_id},
        )
        docs = result.fetchall()

    return [
        {
            "document_id": str(d[0]),
            "filename": d[1],
            "current_node": d[3] or "queued",
            "current_status": d[4] or d[2],
            "detail": d[5] or "",
            "last_event": str(d[6]) if d[6] else "",
        }
        for d in docs
    ]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "monitoring"}

# Uses redis.asyncio for non-blocking SSE streaming

# all-documents endpoint for recent uploads
