from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.core.agents.llm_client import LLMClient
from app.core.agents.narrative_architect import build_outline
from app.core.agents.prompt_registry import PromptRegistry
from app.core.agents.slide_reviser import revise_slide
from app.core.agents.slot_filler import build_fallback_slide, fill_slide
from app.core.agents.visual_policy import apply_visual_policy
from app.core.auditor.audit_runner import run_full_audit
from app.core.builder.pptx_builder import PptxBuilder
from app.core.exporter.html_exporter import export_html_viewer
from app.core.exporter.pdf_exporter import convert_to_pdf
from app.core.exporter.preview_renderer import render_previews
from app.core.parser.template_parser import TemplateParser
from app.models.audit_report import AuditReport
from app.models.outline import Outline
from app.models.presentation_ir import PresentationIR, SlideIR
from app.models.template_manifest import TemplateManifest

logger = logging.getLogger(__name__)

VARIANTS: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")
ProgressCallback = Callable[[str, int], None]


class PipelineTimeoutError(Exception):
    pass


@dataclass
class VariantResult:
    variant: str
    pptx_path: str | None = None
    pdf_path: str | None = None
    html_path: str | None = None
    preview_paths: list[str] = field(default_factory=list)
    audit_report: AuditReport | None = None
    presentation_ir: PresentationIR | None = None
    outline: Outline | None = None
    revision: int = 1
    export_state: Literal["READY", "STALE"] = "READY"
    error: str | None = None


@dataclass
class ResultPackage:
    variants: dict[str, VariantResult] = field(default_factory=dict)
    template_manifest: TemplateManifest | None = None


class Dependencies:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = LLMClient(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key
        )
        self.registry = PromptRegistry(skills_dir=settings.skills_dir)
        self.builder = PptxBuilder()
        self.llm_semaphore = asyncio.Semaphore(settings.llm_max_concurrency)


async def generate_deck(
    brief: str,
    template_path: str,
    deps: Dependencies,
    progress_callback: ProgressCallback | None = None,
) -> ResultPackage:
    try:
        return await asyncio.wait_for(
            _generate_deck_inner(brief, template_path, deps, progress_callback),
            timeout=deps.settings.pipeline_timeout_seconds,
        )
    except TimeoutError:
        raise PipelineTimeoutError(
            f"pipeline exceeded {deps.settings.pipeline_timeout_seconds}s budget"
        ) from None


async def _generate_deck_inner(
    brief: str,
    template_path: str,
    deps: Dependencies,
    progress_callback: ProgressCallback | None,
) -> ResultPackage:
    if progress_callback:
        progress_callback("analyzing_template", 5)
    t0 = time.monotonic()
    manifest = TemplateParser().parse(template_path)
    logger.info("parsed template in %.1fs", time.monotonic() - t0)
    if progress_callback:
        progress_callback("extracting_design_system", 20)

    variant_results = await asyncio.gather(
        *(
            _build_variant(
                v,
                brief,
                template_path,
                manifest,
                deps,
                progress_callback,
            )
            for v in VARIANTS
        ),
    )
    return ResultPackage(
        variants={r.variant: r for r in variant_results},
        template_manifest=manifest,
    )


async def _build_variant(
    variant: str,
    brief: str,
    template_path: str,
    manifest: TemplateManifest,
    deps: Dependencies,
    progress_callback: ProgressCallback | None = None,
) -> VariantResult:
    try:
        if progress_callback:
            progress_callback("planning_structure", 30)
        outline = await build_outline(
            brief,
            manifest,
            variant,
            deps.llm,
            deps.registry,
            deps.settings.llm_model,
            deps.settings.n_slides_min,
            deps.settings.n_slides_max,
        )

        async def _fill(item):
            async with deps.llm_semaphore:
                return await fill_slide(
                    item,
                    variant,
                    manifest,
                    brief,
                    deps.llm,
                    deps.registry,
                    deps.settings.llm_model,
                    deps.settings.slot_filler_max_retries,
                )

        if progress_callback:
            progress_callback("matching_layouts", 45)
        fill_results = await asyncio.gather(
            *(_fill(item) for item in outline.items), return_exceptions=True
        )

        slides = []
        for item, res in zip(outline.items, fill_results):
            if isinstance(res, Exception):
                logger.warning(
                    "using deterministic fallback for slide %s after fill failure: %s",
                    item.slide_index,
                    res,
                )
                slides.append(build_fallback_slide(item, manifest))
                continue
            slides.append(res)

        if len(slides) < deps.settings.n_slides_min:
            raise ValueError(
                f"variant {variant} has only {len(slides)} slides after drops, "
                f"below minimum {deps.settings.n_slides_min}"
            )

        ir = PresentationIR(
            variant=variant, template_source_hash=manifest.source_hash, slides=slides
        )
        ir = apply_visual_policy(ir)

        if progress_callback:
            progress_callback("building_variants", 60)
        build_result = deps.builder.build(template_path, manifest, ir)
        if progress_callback:
            progress_callback("auditing", 80)
        audit_report = await run_full_audit(
            build_result,
            ir,
            manifest,
            brief,
            deps.llm,
            deps.registry,
            deps.settings.llm_model,
        )

        output_dir = str(Path(build_result.pptx_path).parent)
        pdf_path: str | None = None
        preview_paths: list[str] = []
        try:
            if progress_callback:
                progress_callback("exporting", 90)
            pdf_path = await convert_to_pdf(
                build_result.pptx_path,
                output_dir,
                deps.settings.pdf_export_timeout_seconds,
            )
            preview_paths = render_previews(pdf_path, output_dir)
        except Exception as exc:  # noqa: BLE001 — PDF/preview export is best-effort; the native .pptx is the deliverable
            logger.warning(
                "variant %s: pdf export failed, keeping pptx only: %s", variant, exc
            )

        html_path: str | None = None
        if preview_paths:
            html_path = export_html_viewer(
                preview_paths,
                str(Path(output_dir) / f"variant_{variant}.html"),
                title=f"Presentation — variant {variant}",
            )

        return VariantResult(
            variant=variant,
            pptx_path=build_result.pptx_path,
            pdf_path=pdf_path,
            html_path=html_path,
            preview_paths=preview_paths,
            audit_report=audit_report,
            presentation_ir=ir,
            outline=outline,
        )
    except Exception as exc:
        logger.exception("variant %s failed", variant)
        return VariantResult(variant=variant, error=str(exc))


async def revise_variant_slide(
    *,
    variant_result: VariantResult,
    slide_position: int,
    brief: str,
    instructions: str,
    template_path: str,
    manifest: TemplateManifest,
    deps: Dependencies,
) -> VariantResult:
    """Create a new revision of one slide and rebuild the affected variant."""

    if variant_result.presentation_ir is None:
        raise ValueError("variant has no presentation IR")
    ir = variant_result.presentation_ir
    if slide_position < 1 or slide_position > len(ir.slides):
        raise IndexError(f"slide position {slide_position} is out of range")

    current_slide = ir.slides[slide_position - 1]
    revised_slide = await revise_slide(
        current_slide=current_slide,
        brief=brief,
        instructions=instructions,
        manifest=manifest,
        llm=deps.llm,
        registry=deps.registry,
        model=deps.settings.llm_model,
        max_retries=deps.settings.slot_filler_max_retries,
    )
    return await rebuild_variant_with_slide(
        variant_result=variant_result,
        slide_position=slide_position,
        replacement_slide=revised_slide,
        brief=brief,
        template_path=template_path,
        manifest=manifest,
        deps=deps,
    )


async def rebuild_variant_with_slide(
    *,
    variant_result: VariantResult,
    slide_position: int,
    replacement_slide: SlideIR,
    brief: str,
    template_path: str,
    manifest: TemplateManifest,
    deps: Dependencies,
) -> VariantResult:
    """Rebuild a variant after replacing one semantic slide."""

    if variant_result.presentation_ir is None:
        raise ValueError("variant has no presentation IR")
    ir = variant_result.presentation_ir
    if slide_position < 1 or slide_position > len(ir.slides):
        raise IndexError(f"slide position {slide_position} is out of range")
    slides = list(ir.slides)
    slides[slide_position - 1] = replacement_slide.model_copy(
        update={"slide_index": slides[slide_position - 1].slide_index}
    )
    # A slide-level revision must not silently rewrite neighbouring slides.
    revised_ir = ir.model_copy(update={"slides": slides})

    build_result = deps.builder.build(template_path, manifest, revised_ir)
    audit_report = await run_full_audit(
        build_result,
        revised_ir,
        manifest,
        brief,
        deps.llm,
        deps.registry,
        deps.settings.llm_model,
    )
    output_dir = str(Path(build_result.pptx_path).parent)
    pdf_path: str | None = None
    preview_paths: list[str] = []
    try:
        pdf_path = await convert_to_pdf(
            build_result.pptx_path,
            output_dir,
            deps.settings.pdf_export_timeout_seconds,
        )
        preview_paths = render_previews(pdf_path, output_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slide revision export failed, keeping pptx only: %s", exc)

    html_path: str | None = None
    if preview_paths:
        html_path = export_html_viewer(
            preview_paths,
            str(Path(output_dir) / f"variant_{variant_result.variant}.html"),
            title=f"Presentation — variant {variant_result.variant}",
        )

    return VariantResult(
        variant=variant_result.variant,
        pptx_path=build_result.pptx_path,
        pdf_path=pdf_path,
        html_path=html_path,
        preview_paths=preview_paths,
        audit_report=audit_report,
        presentation_ir=revised_ir,
        outline=variant_result.outline,
        revision=variant_result.revision + 1,
    )
