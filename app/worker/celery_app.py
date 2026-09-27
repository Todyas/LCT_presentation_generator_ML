from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "presentation_generator",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=settings.pipeline_timeout_seconds + 120,
    task_soft_time_limit=settings.pipeline_timeout_seconds + 60,
    broker_connection_retry_on_startup=True,
)
