from __future__ import annotations

import io
import shutil
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
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api.schemas import (
    HealthResponse,
    JobCreatedResponse,
    JobStatusResponse,
    JobSummaryDTO,
    VariantResultDTO,
)
from app.config import Settings, get_settings
from app.pipeline.jobs import JobStatus, JobStore
from app.pipeline.orchestrator import Dependencies, generate_deck

router = APIRouter()
job_store = JobStore()


def _validate_pptx_upload(raw_bytes: bytes, max_bytes: int) -> None:
    if len(raw_bytes) > max_bytes:
        raise HTTPException(413, f"template exceeds max upload size of {max_bytes} bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            if "ppt/presentation.xml" not in zf.namelist():
                raise HTTPException(422, "not a valid .pptx file (missing ppt/presentation.xml)")
    except zipfile.BadZipFile:
        raise HTTPException(422, "not a valid .pptx file (not a zip archive)")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.post("/generate", response_model=JobCreatedResponse, status_code=202)
async def generate(
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    template: Annotated[UploadFile, File()],
    brief: Annotated[str, Form(min_length=10, max_length=5000)],
) -> JobCreatedResponse:
    raw_bytes = await template.read()
    _validate_pptx_upload(raw_bytes, settings.max_upload_bytes)

    job = job_store.create(template_filename=template.filename or "template.pptx")
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


@router.get("/jobs", response_model=list[JobSummaryDTO])
async def list_jobs() -> list[JobSummaryDTO]:
    return [
        JobSummaryDTO(
            job_id=j.job_id,
            status=j.status.value,
            created_at=j.created_at,
            template_filename=j.template_filename,
        )
        for j in job_store.list_all()
    ]


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: str, settings: Annotated[Settings, Depends(get_settings)]
) -> Response:
    job = job_store.delete(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    storage_dir = Path(settings.storage_dir) / job_id
    shutil.rmtree(storage_dir, ignore_errors=True)
    return Response(status_code=204)


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


@router.get("/jobs/{job_id}/audit/{variant}")
async def get_job_audit(job_id: str, variant: str) -> JSONResponse:
    job = job_store.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "job not found or not finished")
    variant_result = job.result.variants.get(variant)
    if variant_result is None or variant_result.audit_report is None:
        raise HTTPException(404, f"audit report not available for variant {variant!r}")
    return JSONResponse(content=variant_result.audit_report.model_dump(mode="json"))


@router.get("/jobs/{job_id}/download")
async def download_job_bundle(job_id: str) -> Response:
    job = job_store.get(job_id)
    if job is None or job.result is None:
        raise HTTPException(404, "job not found or not finished")

    buffer = io.BytesIO()
    files_added = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for variant, vr in job.result.variants.items():
            if vr.pptx_path is not None:
                zf.write(vr.pptx_path, f"variant_{variant}.pptx")
                files_added += 1
            if vr.pdf_path is not None:
                zf.write(vr.pdf_path, f"variant_{variant}.pdf")
                files_added += 1
            if vr.audit_report is not None:
                zf.writestr(f"audit_{variant}.json", vr.audit_report.model_dump_json(indent=2))
                files_added += 1

    if files_added == 0:
        raise HTTPException(404, "no files available for this job")

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="job_{job_id}.zip"'},
    )
