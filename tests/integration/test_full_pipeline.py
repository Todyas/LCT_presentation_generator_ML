import shutil
import time
from pathlib import Path
from unittest import mock

from pptx import Presentation

from app.config import Settings
from app.core.agents.slot_filler import SlotFillError
from app.core.auditor.semantic_audit import SemanticFindings
from app.models.outline import Outline, OutlineItem
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    SlideIR,
    TitleComponent,
)
from app.models.template_manifest import LayoutType
from app.pipeline.orchestrator import Dependencies, generate_deck

FIXTURE_PATH = "tests/fixtures/templates/generated_minimal.pptx"


class FakeLLM:
    def __init__(self, n_items: int = 13) -> None:
        self.n_items = n_items

    async def complete_structured(
        self, model, system_prompt, user_prompt, response_model, model_params
    ):
        if response_model is Outline:
            return Outline(
                variant="A",
                items=[
                    OutlineItem(
                        slide_index=i,
                        working_title=f"Slide {i}",
                        key_message=f"Key message {i}",
                        suggested_layout_type=LayoutType.CONTENT_1COL,
                        content_hint=f"Content hint {i}",
                    )
                    for i in range(self.n_items)
                ],
            )
        if response_model is SlideIR:
            import re

            match = re.search(r"slide_index:\s*(\d+)", user_prompt)
            idx = int(match.group(1)) if match else 0
            return SlideIR(
                slide_index=idx,
                layout_type=LayoutType.CONTENT_1COL,
                title=TitleComponent(text=f"Generated title {idx}"),
                components=[
                    BulletBlock(items=[BulletItem(text="A short bullet point.")])
                ],
            )
        if response_model is SemanticFindings:
            return SemanticFindings(findings=[])
        raise AssertionError(f"unexpected response_model: {response_model}")


def _valid_slide(slide_index: int) -> SlideIR:
    return SlideIR(
        slide_index=slide_index,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text=f"Slide {slide_index} title"),
        components=[BulletBlock(items=[BulletItem(text="A short bullet.")])],
    )


def _copy_template(tmp_path: Path) -> str:
    dest = tmp_path / "template.pptx"
    shutil.copyfile(FIXTURE_PATH, dest)
    return str(dest)


def _deps(settings: Settings | None = None) -> Dependencies:
    deps = Dependencies(settings or Settings())
    deps.llm = FakeLLM()
    return deps


def _patched_exporters():
    return (
        mock.patch(
            "app.pipeline.orchestrator.convert_to_pdf",
            new=mock.AsyncMock(return_value="/fake/path.pdf"),
        ),
        mock.patch(
            "app.pipeline.orchestrator.render_previews",
            new=mock.Mock(return_value=[]),
        ),
    )


async def test_pipeline_completes_under_timeout(tmp_path):
    template_path = _copy_template(tmp_path)
    deps = _deps()
    progress_events = []

    patch_pdf, patch_preview = _patched_exporters()
    t0 = time.monotonic()
    with patch_pdf, patch_preview:
        result = await generate_deck(
            "A detailed corporate brief about our quarterly plan.",
            template_path,
            deps,
            progress_callback=lambda stage, progress: progress_events.append(
                (stage, progress)
            ),
        )
    elapsed = time.monotonic() - t0

    assert elapsed < deps.settings.pipeline_timeout_seconds
    for variant in ("A", "B", "C"):
        variant_result = result.variants[variant]
        assert variant_result.error is None
        assert Path(variant_result.pptx_path).exists()
    assert ("analyzing_template", 5) in progress_events
    assert ("exporting", 90) in progress_events


async def test_single_slide_failure_does_not_fail_variant(tmp_path):
    template_path = _copy_template(tmp_path)
    deps = _deps()

    async def fake_fill_slide(
        item, variant, manifest, brief, llm, registry, model, max_retries
    ):
        if item.slide_index == 0:
            raise SlotFillError(item.slide_index, ValueError("boom"))
        return _valid_slide(item.slide_index)

    patch_pdf, patch_preview = _patched_exporters()
    with (
        patch_pdf,
        patch_preview,
        mock.patch("app.pipeline.orchestrator.fill_slide", new=fake_fill_slide),
    ):
        result = await generate_deck(
            "A detailed corporate brief about our quarterly plan.", template_path, deps
        )

    variant_result = result.variants["A"]
    assert variant_result.error is None
    slide_count = len(list(Presentation(variant_result.pptx_path).slides))
    assert slide_count == 12


async def test_variant_dropping_below_minimum_fails_only_that_variant(tmp_path):
    template_path = _copy_template(tmp_path)
    deps = _deps()

    async def fake_fill_slide(
        item, variant, manifest, brief, llm, registry, model, max_retries
    ):
        if variant == "B" and item.slide_index >= 8:
            raise SlotFillError(item.slide_index, ValueError("boom"))
        return _valid_slide(item.slide_index)

    patch_pdf, patch_preview = _patched_exporters()
    with (
        patch_pdf,
        patch_preview,
        mock.patch("app.pipeline.orchestrator.fill_slide", new=fake_fill_slide),
    ):
        result = await generate_deck(
            "A detailed corporate brief about our quarterly plan.", template_path, deps
        )

    assert result.variants["B"].error is not None
    assert result.variants["A"].error is None
    assert result.variants["C"].error is None
