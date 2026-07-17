"""Celery application configuration."""
import os

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"

from celery import Celery
from config import get_settings

settings = get_settings()


def _fix_redis_url(url: str) -> str:
    if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
        separator = "&" if "?" in url else "?"
        url += f"{separator}ssl_cert_reqs=none"
    return url


redis_url = _fix_redis_url(settings.REDIS_URL)

celery_app = Celery(
    "meddocs",
    broker=redis_url,
    backend=redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Import tasks to register them
import workers.tasks  # noqa
