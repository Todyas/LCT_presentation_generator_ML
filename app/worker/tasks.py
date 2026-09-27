import asyncio

from app.config import get_settings
from app.pipeline.service import run_generation_job, run_revision_job
from app.worker.celery_app import celery_app


@celery_app.task(name="presentation.generate_deck")
def generate_deck_task(job_id: str) -> None:
    asyncio.run(run_generation_job(job_id, get_settings()))


@celery_app.task(name="presentation.revise_slide")
def revise_slide_task(job_id: str) -> None:
    asyncio.run(run_revision_job(job_id, get_settings()))
