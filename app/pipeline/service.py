from __future__ import annotations

from contextlib import asynccontextmanager

from redis.asyncio import Redis

from app.config import Settings
from app.models.presentation_ir import SlideIR
from app.pipeline.jobs import JobStatus, JobStore, JobType, SlideRevisionStore
from app.pipeline.orchestrator import (
    Dependencies,
    generate_deck,
    rebuild_variant_with_slide,
    revise_variant_slide,
)


def _audit_for_slide(variant_result, slide_index: int) -> dict:
    if variant_result.audit_report is None:
        return {"issues": []}
    return {
        "issues": [
            issue.model_dump(mode="json")
            for issue in variant_result.audit_report.issues
            if issue.slide_index == slide_index
        ]
    }


def _save_initial_revisions(
    deck_job_id: str,
    result,
    revisions: SlideRevisionStore,
) -> None:
    for code, variant in result.variants.items():
        if variant.presentation_ir is None:
            continue
        for position, slide in enumerate(variant.presentation_ir.slides, start=1):
            preview = (
                variant.preview_paths[position - 1]
                if position <= len(variant.preview_paths)
                else None
            )
            revisions.save(
                deck_job_id=deck_job_id,
                variant=code,
                slide_position=position,
                revision_number=1,
                correction_prompt="",
                slide=slide,
                preview_path=preview,
                audit_json=_audit_for_slide(variant, slide.slide_index),
            )


async def run_generation_job(job_id: str, settings: Settings) -> None:
    store = JobStore(
        settings.database_url, initialize_schema=not settings.task_queue_enabled
    )
    revisions = SlideRevisionStore(
        settings.database_url, initialize_schema=not settings.task_queue_enabled
    )
    job = store.get(job_id)
    if job is None or job.status in {JobStatus.DONE, JobStatus.PARTIAL}:
        return
    store.update(
        job_id, status=JobStatus.RUNNING, stage="analyzing_template", progress=5
    )
    try:
        deps = Dependencies(
            settings.model_copy(
                update={
                    "n_slides_min": job.slide_count,
                    "n_slides_max": job.slide_count,
                }
            )
        )

        def report_progress(stage: str, progress: int) -> None:
            current = store.get(job_id)
            if current is not None and progress >= current.progress:
                store.update(job_id, stage=stage, progress=progress)

        generation_brief = (
            f"{job.brief}\n\nКонтекст презентации: назначение={job.purpose}; "
            f"язык={job.language}; стиль={job.style}."
        )
        result = await generate_deck(
            generation_brief,
            job.template_path,
            deps,
            progress_callback=report_progress,
        )
        successful = sum(
            1 for variant in result.variants.values() if variant.error is None
        )
        if successful == 0:
            store.update(
                job_id,
                status=JobStatus.FAILED,
                stage="failed",
                progress=100,
                result=result,
                error="all presentation variants failed",
            )
            return
        _save_initial_revisions(job_id, result, revisions)
        store.update(
            job_id,
            status=(
                JobStatus.DONE
                if successful == len(result.variants)
                else JobStatus.PARTIAL
            ),
            stage="completed" if successful == len(result.variants) else "partial",
            progress=100,
            result=result,
        )
    except Exception as exc:  # noqa: BLE001
        if store.get(job_id) is not None:
            store.update(
                job_id,
                status=JobStatus.FAILED,
                stage="failed",
                progress=100,
                error=str(exc),
            )


@asynccontextmanager
async def _revision_lock(child, settings: Settings):
    if not settings.task_queue_enabled:
        yield
        return
    client = Redis.from_url(settings.redis_url)
    lock = client.lock(
        f"slide-revision:{child.parent_job_id}:{child.variant}:{child.slide_position}",
        timeout=settings.pipeline_timeout_seconds
        + settings.pdf_export_timeout_seconds
        + 60,
        blocking_timeout=settings.pipeline_timeout_seconds,
    )
    acquired = await lock.acquire()
    if not acquired:
        await client.aclose()
        raise TimeoutError("another revision of this slide is still running")
    try:
        yield
    finally:
        await lock.release()
        await client.aclose()


async def run_revision_job(job_id: str, settings: Settings) -> None:
    store = JobStore(
        settings.database_url, initialize_schema=not settings.task_queue_enabled
    )
    revisions = SlideRevisionStore(
        settings.database_url, initialize_schema=not settings.task_queue_enabled
    )
    child = store.get(job_id)
    if (
        child is None
        or child.status == JobStatus.DONE
        or child.parent_job_id is None
        or child.variant is None
    ):
        return
    store.update(job_id, status=JobStatus.RUNNING, stage="revising_slide", progress=10)
    try:
        async with _revision_lock(child, settings):
            parent = store.get(child.parent_job_id)
            if parent is None or parent.result is None:
                raise ValueError("parent deck is not available")
            variant = parent.result.variants.get(child.variant)
            if variant is None or variant.presentation_ir is None:
                raise ValueError("variant is not available")
            if child.slide_position is None:
                raise ValueError("slide position is missing")
            existing_revision = revisions.get_by_source_job(job_id)
            if (
                existing_revision is not None
                and variant.revision >= existing_revision.revision_number
            ):
                variant.export_state = "READY"
                store.update(parent.job_id, result=parent.result)
                store.update(
                    job_id,
                    status=JobStatus.DONE,
                    stage="completed",
                    progress=100,
                    output={
                        "parent_job_id": parent.job_id,
                        "variant": child.variant,
                        "slide_position": child.slide_position,
                        "revision": existing_revision.revision_number,
                        "revision_id": existing_revision.revision_id,
                    },
                )
                return
            request = child.revision_request or {}
            base_revision = request.get("base_revision")
            if base_revision is not None and base_revision != variant.revision:
                raise ValueError(
                    f"revision conflict: expected {base_revision}, current {variant.revision}"
                )
            manifest = parent.result.template_manifest
            if manifest is None:
                raise ValueError("template analysis is not available")
            deps = Dependencies(
                settings.model_copy(
                    update={
                        "n_slides_min": parent.slide_count,
                        "n_slides_max": parent.slide_count,
                    }
                )
            )
            if child.job_type == JobType.ACTIVATE_REVISION:
                stored = revisions.get(request["revision_id"])
                if stored is None:
                    raise ValueError("revision not found")
                if (
                    stored.deck_job_id != parent.job_id
                    or stored.variant != child.variant
                    or stored.slide_position != child.slide_position
                ):
                    raise ValueError("revision does not belong to this slide")
                replacement = SlideIR.model_validate(stored.semantic_ir)
                revised = await rebuild_variant_with_slide(
                    variant_result=variant,
                    slide_position=child.slide_position,
                    replacement_slide=replacement,
                    brief=parent.brief,
                    template_path=parent.template_path,
                    manifest=manifest,
                    deps=deps,
                )
                correction_prompt = f"activate revision {stored.revision_number}"
            else:
                correction_prompt = request.get("instructions", "Улучши слайд")
                revised = await revise_variant_slide(
                    variant_result=variant,
                    slide_position=child.slide_position,
                    brief=parent.brief,
                    instructions=correction_prompt,
                    template_path=parent.template_path,
                    manifest=manifest,
                    deps=deps,
                )

            slide = revised.presentation_ir.slides[child.slide_position - 1]
            preview = (
                revised.preview_paths[child.slide_position - 1]
                if child.slide_position <= len(revised.preview_paths)
                else None
            )
            saved = revisions.save(
                deck_job_id=parent.job_id,
                variant=child.variant,
                slide_position=child.slide_position,
                revision_number=revised.revision,
                source_job_id=job_id,
                correction_prompt=correction_prompt,
                slide=slide,
                preview_path=preview,
                audit_json=_audit_for_slide(revised, slide.slide_index),
            )
            parent.result.variants[child.variant] = revised
            store.update(parent.job_id, result=parent.result)
            store.update(
                job_id,
                status=JobStatus.DONE,
                stage="completed",
                progress=100,
                output={
                    "parent_job_id": parent.job_id,
                    "variant": child.variant,
                    "slide_position": child.slide_position,
                    "revision": revised.revision,
                    "revision_id": saved.revision_id,
                },
            )
    except Exception as exc:  # noqa: BLE001
        parent = store.get(child.parent_job_id)
        if parent is not None and parent.result is not None:
            failed_variant = parent.result.variants.get(child.variant)
            if failed_variant is not None:
                failed_variant.export_state = "READY"
                store.update(parent.job_id, result=parent.result)
        store.update(
            job_id,
            status=JobStatus.FAILED,
            stage="failed",
            progress=100,
            error=str(exc),
        )
