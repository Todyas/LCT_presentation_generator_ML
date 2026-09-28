"""Coverage for app.pipeline.service.run_revision_job (REVISE_SLIDE and
ACTIVATE_REVISION), built on top of a real generated deck.
"""

from __future__ import annotations

import asyncio

import pytest

from app.models.outline import Outline
from app.models.presentation_ir import SlideIR, TitleComponent
from app.pipeline import service as service_module
from app.pipeline.jobs import JobStatus, JobStore, JobType, SlideRevisionStore
from tests.factories import adaptive_slide_response, make_outline

SLIDE_COUNT = 12


def _patch_service_dependencies(monkeypatch, scripted_llm):
    real_dependencies_cls = service_module.Dependencies

    def _factory(settings):
        deps = real_dependencies_cls(settings)
        deps.llm = scripted_llm
        return deps

    monkeypatch.setattr(service_module, "Dependencies", _factory)


async def _generate_seed_deck(
    store: JobStore, settings, template_path: str, scripted_llm
) -> str:
    scripted_llm.script(Outline, make_outline(SLIDE_COUNT))
    scripted_llm.script(SlideIR, adaptive_slide_response)
    job = store.create(
        template_filename="template.pptx",
        template_path=template_path,
        brief="Бриф без чисел для генерации колоды.",
        purpose="project",
        slide_count=SLIDE_COUNT,
        language="ru",
        style="balanced",
    )
    await service_module.run_generation_job(job.job_id, settings)
    seeded = store.get(job.job_id)
    assert seeded.status == JobStatus.DONE
    return job.job_id


def _mark_stale(store: JobStore, parent_job_id: str, variant: str) -> None:
    parent = store.get(parent_job_id)
    parent.result.variants[variant].export_state = "STALE"
    store.update(parent_job_id, result=parent.result)


def _create_revise_child(
    store: JobStore,
    *,
    parent_job_id: str,
    variant: str,
    slide_position: int,
    instructions: str,
    base_revision: int,
):
    return store.create(
        template_filename="template.pptx",
        job_type=JobType.REVISE_SLIDE,
        parent_job_id=parent_job_id,
        variant=variant,
        slide_position=slide_position,
        revision_request={"instructions": instructions, "base_revision": base_revision},
    )


@pytest.fixture
async def seeded_deck(
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
    parent_job_id = await _generate_seed_deck(
        store, test_settings, default_template, scripted_llm
    )
    return {
        "store": store,
        "revisions": revisions,
        "settings": test_settings,
        "parent_job_id": parent_job_id,
        "scripted_llm": scripted_llm,
    }


# --------------------------------------------------------------------------- #
# REVISE_SLIDE happy path
# --------------------------------------------------------------------------- #


async def test_revise_slide_bumps_revision_and_only_changes_the_target_slide(
    seeded_deck,
):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    revisions = seeded_deck["revisions"]
    before = store.get(parent_job_id).result.variants["A"]
    other_slides_before = [
        s.model_dump(mode="json")
        for i, s in enumerate(before.presentation_ir.slides)
        if i != 2
    ]

    _mark_stale(store, parent_job_id, "A")
    child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="Сократи текст слайда.",
        base_revision=before.revision,
    )
    await service_module.run_revision_job(child.job_id, settings)

    parent = store.get(parent_job_id)
    after = parent.result.variants["A"]
    assert after.revision == before.revision + 1
    assert after.export_state == "READY"
    other_slides_after = [
        s.model_dump(mode="json")
        for i, s in enumerate(after.presentation_ir.slides)
        if i != 2
    ]
    assert other_slides_after == other_slides_before
    assert after.presentation_ir.slides[2].model_dump(
        mode="json"
    ) != before.presentation_ir.slides[2].model_dump(mode="json")

    child_after = store.get(child.job_id)
    assert child_after.status == JobStatus.DONE
    assert child_after.output["parent_job_id"] == parent_job_id
    assert child_after.output["variant"] == "A"
    assert child_after.output["slide_position"] == 3
    assert child_after.output["revision"] == after.revision

    active = revisions.list(parent_job_id, "A", 3)
    assert sum(item.is_active for item in active) == 1
    newest = max(active, key=lambda item: item.revision_number)
    assert newest.is_active is True
    assert newest.revision_number == after.revision
    for item in active:
        if item.revision_id != newest.revision_id:
            assert item.is_active is False


# --------------------------------------------------------------------------- #
# ACTIVATE_REVISION
# --------------------------------------------------------------------------- #


async def test_activate_revision_restores_original_content_without_an_llm_call(
    seeded_deck,
):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    revisions = seeded_deck["revisions"]
    scripted_llm = seeded_deck["scripted_llm"]
    original_slide = (
        store.get(parent_job_id)
        .result.variants["A"]
        .presentation_ir.slides[2]
        .model_dump(mode="json")
    )
    original_revision_id = revisions.list(parent_job_id, "A", 3)[0].revision_id

    _mark_stale(store, parent_job_id, "A")
    edit_child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="Сократи текст слайда.",
        base_revision=1,
    )
    await service_module.run_revision_job(edit_child.job_id, settings)
    revision_after_edit = store.get(parent_job_id).result.variants["A"].revision
    # rebuild_variant_with_slide always re-runs the (here: always-degrading)
    # semantic audit regardless of edit vs activation, so only the
    # slide_reviser's own SlideIR calls are a meaningful "no LLM call" signal.
    slide_ir_calls_before_activation = sum(
        1 for c in scripted_llm.calls if c.response_model is SlideIR
    )

    _mark_stale(store, parent_job_id, "A")
    activate_child = store.create(
        template_filename="template.pptx",
        job_type=JobType.ACTIVATE_REVISION,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        revision_request={
            "revision_id": original_revision_id,
            "base_revision": revision_after_edit,
        },
    )
    await service_module.run_revision_job(activate_child.job_id, settings)

    slide_ir_calls_after_activation = sum(
        1 for c in scripted_llm.calls if c.response_model is SlideIR
    )
    assert slide_ir_calls_after_activation == slide_ir_calls_before_activation
    parent = store.get(parent_job_id)
    after = parent.result.variants["A"]
    assert after.revision == revision_after_edit + 1
    assert after.presentation_ir.slides[2].model_dump(mode="json") == original_slide
    assert store.get(activate_child.job_id).status == JobStatus.DONE


# --------------------------------------------------------------------------- #
# base_revision mismatch
# --------------------------------------------------------------------------- #


async def test_base_revision_mismatch_fails_child_and_restores_ready(seeded_deck):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    _mark_stale(store, parent_job_id, "A")
    child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="Сократи текст слайда.",
        base_revision=999,
    )

    await service_module.run_revision_job(child.job_id, settings)

    child_after = store.get(child.job_id)
    assert child_after.status == JobStatus.FAILED
    assert "revision conflict" in child_after.error
    parent = store.get(parent_job_id)
    assert parent.result.variants["A"].export_state == "READY"


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


async def test_running_the_same_child_job_twice_does_not_create_a_second_revision(
    seeded_deck,
):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    revisions = seeded_deck["revisions"]
    _mark_stale(store, parent_job_id, "A")
    child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="Сократи текст слайда.",
        base_revision=1,
    )

    await service_module.run_revision_job(child.job_id, settings)
    revision_count_after_first = len(revisions.list(parent_job_id, "A", 3))
    revision_after_first_run = store.get(parent_job_id).result.variants["A"].revision

    await service_module.run_revision_job(child.job_id, settings)

    assert len(revisions.list(parent_job_id, "A", 3)) == revision_count_after_first
    assert (
        store.get(parent_job_id).result.variants["A"].revision
        == revision_after_first_run
    )
    assert store.get(child.job_id).status == JobStatus.DONE


# --------------------------------------------------------------------------- #
# LLM failure
# --------------------------------------------------------------------------- #


async def test_llm_failure_fails_child_and_restores_parent_to_ready(seeded_deck):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    scripted_llm = seeded_deck["scripted_llm"]
    scripted_llm.replace(SlideIR, RuntimeError("llm backend unavailable"))

    _mark_stale(store, parent_job_id, "A")
    child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="Сократи текст слайда.",
        base_revision=1,
    )

    await service_module.run_revision_job(child.job_id, settings)

    child_after = store.get(child.job_id)
    assert child_after.status == JobStatus.FAILED
    parent = store.get(parent_job_id)
    assert parent.result.variants["A"].export_state == "READY"


# --------------------------------------------------------------------------- #
# Known bug §12.4: concurrent edits of different slides of the same variant
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True,
    reason="BUG: the Redis lock key is per-slide (slide-revision:{parent}:{V}:{pos}), "
    "not per-variant, and fallback mode has no lock at all (service.py:131-151), so "
    "concurrent edits of two different slides of the same variant race on the shared "
    "built_{V}.pptx and parent.result write — the later writer silently discards the "
    "earlier edit; see ARCHITECTURE.md §12.4.",
)
async def test_concurrent_edits_of_different_slides_of_the_same_variant_both_survive(
    seeded_deck,
):
    store, settings, parent_job_id = (
        seeded_deck["store"],
        seeded_deck["settings"],
        seeded_deck["parent_job_id"],
    )
    scripted_llm = seeded_deck["scripted_llm"]

    slow_started = asyncio.Event()
    fast_done = asyncio.Event()

    async def _gated_response(user_prompt: str) -> SlideIR:
        if "MARK_SLOW" in user_prompt:
            slow_started.set()
            await fast_done.wait()
            slide = adaptive_slide_response(user_prompt)
            return slide.model_copy(
                update={"title": TitleComponent(text="Slow edit applied")}
            )
        if "MARK_FAST" in user_prompt:
            await slow_started.wait()
            slide = adaptive_slide_response(user_prompt)
            result = slide.model_copy(
                update={"title": TitleComponent(text="Fast edit applied")}
            )
            fast_done.set()
            return result
        return adaptive_slide_response(user_prompt)

    scripted_llm.script(SlideIR, _gated_response)

    _mark_stale(store, parent_job_id, "A")
    slow_child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=3,
        instructions="MARK_SLOW: сократи текст слайда.",
        base_revision=1,
    )
    fast_child = _create_revise_child(
        store,
        parent_job_id=parent_job_id,
        variant="A",
        slide_position=5,
        instructions="MARK_FAST: сократи текст слайда.",
        base_revision=1,
    )

    await asyncio.gather(
        service_module.run_revision_job(slow_child.job_id, settings),
        service_module.run_revision_job(fast_child.job_id, settings),
    )

    final_ir = store.get(parent_job_id).result.variants["A"].presentation_ir
    assert final_ir.slides[2].title.text == "Slow edit applied"
    assert final_ir.slides[4].title.text == "Fast edit applied"
