from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse

from app.api.schemas import JobCreatedResponse, JobStatusResponse, VariantResultDTO
from app.config import Settings, get_settings
from app.pipeline.jobs import JobStatus, JobStore
from app.pipeline.orchestrator import Dependencies, generate_deck

router = APIRouter()
job_store = JobStore()


def _validate_pptx_upload(raw_bytes: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            if "ppt/presentation.xml" not in zf.namelist():
                raise HTTPException(422, "not a valid .pptx file (missing ppt/presentation.xml)")
    except zipfile.BadZipFile:
        raise HTTPException(422, "not a valid .pptx file (not a zip archive)")


@router.post("/generate", response_model=JobCreatedResponse, status_code=202)
async def generate(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    template: Annotated[UploadFile, File()],
    brief: Annotated[str, Form(min_length=10, max_length=5000)],
) -> JobCreatedResponse:
    raw_bytes = await template.read()
    _validate_pptx_upload(raw_bytes)

    job = job_store.create()
    storage_dir = Path(settings.storage_dir) / job.job_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    template_path = storage_dir / "template.pptx"
    template_path.write_bytes(raw_bytes)

    background_tasks.add_task(_run_job, job.job_id, brief, str(template_path), settings)
    return JobCreatedResponse(job_id=job.job_id)


async def _run_job(job_id: str, brief: str, template_path: str, settings: Settings) -> None:
    job_store.update(job_id, status=JobStatus.RUNNING)
    try:
        deps = Dependencies(settings)
        result = await generate_deck(brief, template_path, deps)
        job_store.update(job_id, status=JobStatus.DONE, result=result)
    except Exception as exc:  # noqa: BLE001 — a failed job must be reported via status, never crash the background task
        job_store.update(job_id, status=JobStatus.FAILED, error=str(exc))


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    variants: list[VariantResultDTO] = []
    if job.result is not None:
        for v in job.result.variants.values():
            variants.append(
                VariantResultDTO(
                    variant=v.variant,
                    pptx_available=v.pptx_path is not None,
                    pdf_available=v.pdf_path is not None,
                    preview_count=len(v.preview_paths),
                    audit_passed=v.audit_report.passed if v.audit_report else None,
                    error=v.error,
                )
            )

    return JobStatusResponse(
        job_id=job.job_id, status=job.status.value, variants=variants, error=job.error
    )


@router.get("/jobs/{job_id}/files/{variant}/{kind}")
async def get_job_file(job_id: str, variant: str, kind: str) -> FileResponse:
    job = job_store.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "job not found or not finished")
    variant_result = job.result.variants.get(variant)
    if variant_result is None:
        raise HTTPException(404, "variant not found")

    path_by_kind = {"pptx": variant_result.pptx_path, "pdf": variant_result.pdf_path}
    if kind not in path_by_kind or path_by_kind[kind] is None:
        raise HTTPException(404, f"file kind {kind!r} not available for variant {variant!r}")
    return FileResponse(path_by_kind[kind])
