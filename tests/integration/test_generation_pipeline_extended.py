"""End-to-end pipeline tests: real builder + real deterministic audit, with
ScriptedLLM standing in for every LLM call and the exporters patched out.
"""

from __future__ import annotations

import asyncio
from unittest import mock

import pytest

from app.core.agents.narrative_architect import VARIANT_DESCRIPTIONS
from app.models.outline import Outline
from app.models.presentation_ir import SlideIR
from app.pipeline import service as service_module
from app.pipeline.jobs import JobStatus, JobStore, SlideRevisionStore
from app.pipeline.orchestrator import (
    Dependencies,
    PipelineTimeoutError,
    generate_deck,
)
from tests.factories import adaptive_slide_response, make_outline

SLIDE_COUNT = 12


def _patch_service_dependencies(monkeypatch, scripted_llm):
    """run_generation_job/run_revision_job build their own Dependencies
    internally, so the fake LLM must be spliced in after construction."""
    real_dependencies_cls = service_module.Dependencies

    def _factory(settings):
        deps = real_dependencies_cls(settings)
        deps.llm = scripted_llm
        return deps

    monkeypatch.setattr(service_module, "Dependencies", _factory)


def _seed_generation_job(
    store: JobStore, *, template_path: str, brief: str, slide_count: int = SLIDE_COUNT
):
    return store.create(
        template_filename="template.pptx",
        template_path=template_path,
        brief=brief,
        purpose="project",
        slide_count=slide_count,
        language="ru",
        style="balanced",
    )


# --------------------------------------------------------------------------- #
# generate_deck end-to-end
# --------------------------------------------------------------------------- #


async def test_generate_deck_produces_three_variants_with_exact_slide_count(
    tmp_cwd, deps, scripted_llm, patched_exporters, default_template
):
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)

    result = await generate_deck(
        "Бриф без чисел для генерации колоды.", default_template, deps
    )

    assert set(result.variants) == {"A", "B", "C"}
    for code, variant in result.variants.items():
        assert variant.error is None, f"variant {code} failed: {variant.error}"
        assert len(variant.presentation_ir.slides) == SLIDE_COUNT


async def test_generate_deck_progress_callback_receives_every_stage(
    tmp_cwd, deps, scripted_llm, patched_exporters, default_template
):
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)
    calls: list[tuple[str, int]] = []

    await generate_deck(
        "Бриф без чисел для генерации колоды.",
        default_template,
        deps,
        progress_callback=lambda stage, progress: calls.append((stage, progress)),
    )

    stages = {stage for stage, _ in calls}
    assert {
        "analyzing_template",
        "extracting_design_system",
        "planning_structure",
        "auditing",
    } <= stages
    assert calls[0] == ("analyzing_template", 5)


# --------------------------------------------------------------------------- #
# Partial / total failure
# --------------------------------------------------------------------------- #


def _mixed_outline_responder(user_prompt: str) -> Outline:
    """Variant B always states an invented number, so build_outline exhausts
    its 3 attempts and raises; A and C get a clean, grounded outline."""
    for code, description in VARIANT_DESCRIPTIONS.items():
        if description not in user_prompt:
            continue
        outline = make_outline(SLIDE_COUNT, variant=code)
        if code != "B":
            return outline
        items = list(outline.items)
        items[0] = items[0].model_copy(
            update={"key_message": items[0].key_message + " — рост на 999%"}
        )
        return outline.model_copy(update={"items": items})
    raise AssertionError("prompt did not contain a recognizable variant description")


async def test_one_variant_outline_failure_yields_partial_status(
    tmp_cwd,
    monkeypatch,
    scripted_llm,
    patched_exporters,
    default_template,
    test_settings,
):
    _patch_service_dependencies(monkeypatch, scripted_llm)
    store = JobStore(test_settings.database_url)
    job = _seed_generation_job(
        store, template_path=default_template, brief="Бриф без чисел."
    )

    scripted_llm.script(Outline, _mixed_outline_responder)
    scripted_llm.script(SlideIR, adaptive_slide_response)

    await service_module.run_generation_job(job.job_id, test_settings)

    updated = store.get(job.job_id)
    assert updated.status == JobStatus.PARTIAL
    assert updated.result.variants["B"].error is not None
    assert updated.result.variants["A"].error is None
    assert updated.result.variants["C"].error is None


async def test_all_variants_failing_yields_failed_status_with_message(
    tmp_cwd,
    monkeypatch,
    scripted_llm,
    patched_exporters,
    default_template,
    test_settings,
):
    _patch_service_dependencies(monkeypatch, scripted_llm)
    store = JobStore(test_settings.database_url)
    job = _seed_generation_job(
        store, template_path=default_template, brief="Бриф без чисел."
    )

    scripted_llm.script(Outline, TimeoutError("llm unavailable"))

    await service_module.run_generation_job(job.job_id, test_settings)

    updated = store.get(job.job_id)
    assert updated.status == JobStatus.FAILED
    assert updated.error == "all presentation variants failed"


# --------------------------------------------------------------------------- #
# Slide-fill failures fall back deterministically
# --------------------------------------------------------------------------- #


async def test_slot_fill_failures_fall_back_to_deterministic_slides(
    tmp_cwd, deps, scripted_llm, patched_exporters, default_template
):
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(
        SlideIR, ValueError("numeric claims absent from the brief: 999")
    )

    result = await generate_deck(
        "Бриф без чисел для генерации колоды.", default_template, deps
    )

    for code, variant in result.variants.items():
        assert variant.error is None, f"variant {code} failed: {variant.error}"
        assert len(variant.presentation_ir.slides) == SLIDE_COUNT


# --------------------------------------------------------------------------- #
# Export failures degrade gracefully
# --------------------------------------------------------------------------- #


async def test_pdf_export_failure_still_succeeds_with_no_exports(
    tmp_cwd, deps, scripted_llm, patch_exporters_with_previews, default_template
):
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)
    patch_exporters_with_previews(0, pdf_raises=RuntimeError("soffice crashed"))

    result = await generate_deck(
        "Бриф без чисел для генерации колоды.", default_template, deps
    )

    for variant in result.variants.values():
        assert variant.error is None
        assert variant.pdf_path is None
        assert variant.preview_paths == []
        assert variant.html_path is None


# --------------------------------------------------------------------------- #
# Timeout
# --------------------------------------------------------------------------- #


async def test_pipeline_timeout_raises_pipeline_timeout_error(
    tmp_cwd, test_settings, scripted_llm, patched_exporters, default_template
):
    settings = test_settings.model_copy(update={"pipeline_timeout_seconds": 0})
    deps_obj = Dependencies(settings)
    deps_obj.llm = scripted_llm

    never_fires = asyncio.Event()

    async def _hang(*args, **kwargs):
        await never_fires.wait()

    scripted_llm.complete_structured = _hang  # type: ignore[method-assign]

    with pytest.raises(PipelineTimeoutError):
        await generate_deck("Бриф без чисел.", default_template, deps_obj)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


async def test_run_generation_job_persists_and_round_trips_through_a_fresh_job_store(
    tmp_cwd,
    monkeypatch,
    scripted_llm,
    patched_exporters,
    default_template,
    test_settings,
):
    _patch_service_dependencies(monkeypatch, scripted_llm)
    store = JobStore(test_settings.database_url)
    job = _seed_generation_job(
        store, template_path=default_template, brief="Бриф без чисел."
    )
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)

    await service_module.run_generation_job(job.job_id, test_settings)

    fresh_store = JobStore(test_settings.database_url, initialize_schema=False)
    reloaded = fresh_store.get(job.job_id)

    assert reloaded.status == JobStatus.DONE
    assert reloaded.result.template_manifest is not None
    for variant in reloaded.result.variants.values():
        assert variant.presentation_ir is not None
        assert len(variant.presentation_ir.slides) == SLIDE_COUNT
        assert variant.audit_report is not None
        assert variant.outline is not None


async def test_save_initial_revisions_creates_revision_one_for_every_slide(
    tmp_cwd,
    monkeypatch,
    scripted_llm,
    patched_exporters,
    default_template,
    test_settings,
):
    _patch_service_dependencies(monkeypatch, scripted_llm)
    store = JobStore(test_settings.database_url)
    revisions = SlideRevisionStore(test_settings.database_url, initialize_schema=False)
    job = _seed_generation_job(
        store, template_path=default_template, brief="Бриф без чисел."
    )
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)

    await service_module.run_generation_job(job.job_id, test_settings)

    updated = store.get(job.job_id)
    for code in updated.result.variants:
        items = revisions.list(job.job_id, code, 1)
        assert len(items) == 1
        assert items[0].revision_number == 1
        assert items[0].is_active is True
        stored_positions = {
            position
            for position in range(1, SLIDE_COUNT + 1)
            if revisions.list(job.job_id, code, position)
        }
        assert stored_positions == set(range(1, SLIDE_COUNT + 1))


# --------------------------------------------------------------------------- #
# Progress is monotonic non-decreasing at the service layer
# --------------------------------------------------------------------------- #


async def test_service_level_progress_updates_are_monotonic_non_decreasing(
    tmp_cwd,
    monkeypatch,
    scripted_llm,
    patched_exporters,
    default_template,
    test_settings,
):
    _patch_service_dependencies(monkeypatch, scripted_llm)
    store = JobStore(test_settings.database_url)
    job = _seed_generation_job(
        store, template_path=default_template, brief="Бриф без чисел."
    )
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)

    original_update = JobStore.update
    recorded_progress: list[int] = []

    def _spy_update(self, job_id, **fields):
        if "progress" in fields:
            recorded_progress.append(fields["progress"])
        return original_update(self, job_id, **fields)

    with mock.patch.object(JobStore, "update", _spy_update):
        await service_module.run_generation_job(job.job_id, test_settings)

    assert recorded_progress[0] == 5
    assert recorded_progress == sorted(recorded_progress)
    assert recorded_progress[-1] == 100
