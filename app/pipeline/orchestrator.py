from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.core.agents.llm_client import LLMClient
from app.core.agents.narrative_architect import build_outline
from app.core.agents.prompt_registry import PromptRegistry
from app.core.agents.slot_filler import SlotFillError, fill_slide
from app.core.auditor.audit_runner import run_full_audit
from app.core.builder.pptx_builder import PptxBuilder
from app.core.exporter.pdf_exporter import convert_to_pdf
from app.core.exporter.preview_renderer import render_previews
from app.core.parser.template_parser import TemplateParser
from app.models.audit_report import AuditReport
from app.models.presentation_ir import PresentationIR
from app.models.template_manifest import TemplateManifest

logger = logging.getLogger(__name__)

VARIANTS: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")


class PipelineTimeoutError(Exception):
    pass


@dataclass
class VariantResult:
    variant: str
    pptx_path: str | None = None
    pdf_path: str | None = None
    preview_paths: list[str] = field(default_factory=list)
    audit_report: AuditReport | None = None
    error: str | None = None


@dataclass
class ResultPackage:
    variants: dict[str, VariantResult] = field(default_factory=dict)


class Dependencies:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = LLMClient(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
        self.registry = PromptRegistry(skills_dir=settings.skills_dir)
        self.builder = PptxBuilder()
        self.llm_semaphore = asyncio.Semaphore(settings.llm_max_concurrency)


async def generate_deck(brief: str, template_path: str, deps: Dependencies) -> ResultPackage:
    try:
        return await asyncio.wait_for(
            _generate_deck_inner(brief, template_path, deps),
            timeout=deps.settings.pipeline_timeout_seconds,
        )
    except TimeoutError:
        raise PipelineTimeoutError(
            f"pipeline exceeded {deps.settings.pipeline_timeout_seconds}s budget"
        ) from None


async def _generate_deck_inner(brief: str, template_path: str, deps: Dependencies) -> ResultPackage:
    t0 = time.monotonic()
    manifest = TemplateParser().parse(template_path)
    logger.info("parsed template in %.1fs", time.monotonic() - t0)

    variant_results = await asyncio.gather(
        *(_build_variant(v, brief, template_path, manifest, deps) for v in VARIANTS),
    )
    return ResultPackage(variants={r.variant: r for r in variant_results})


async def _build_variant(
    variant: str, brief: str, template_path: str, manifest: TemplateManifest, deps: Dependencies
) -> VariantResult:
    try:
        outline = await build_outline(
            brief, manifest, variant, deps.llm, deps.registry, deps.settings.llm_model,
            deps.settings.n_slides_min, deps.settings.n_slides_max,
        )

        async def _fill(item):
            async with deps.llm_semaphore:
                return await fill_slide(
                    item, variant, manifest, brief, deps.llm, deps.registry,
                    deps.settings.llm_model, deps.settings.slot_filler_max_retries,
                )

        fill_results = await asyncio.gather(
            *(_fill(item) for item in outline.items), return_exceptions=True
        )

        slides = []
        for item, res in zip(outline.items, fill_results):
            if isinstance(res, SlotFillError):
                logger.warning("dropping slide %s: %s", item.slide_index, res)
                continue
            if isinstance(res, Exception):
                raise res
            slides.append(res)

        if len(slides) < deps.settings.n_slides_min:
            raise ValueError(
                f"variant {variant} has only {len(slides)} slides after drops, "
                f"below minimum {deps.settings.n_slides_min}"
            )

        ir = PresentationIR(
            variant=variant, template_source_hash=manifest.source_hash, slides=slides
        )

        build_result = deps.builder.build(template_path, manifest, ir)
        audit_report = await run_full_audit(
            build_result, ir, manifest, brief, deps.llm, deps.registry, deps.settings.llm_model
        )

        output_dir = str(Path(build_result.pptx_path).parent)
        pdf_path: str | None = None
        preview_paths: list[str] = []
        try:
            pdf_path = await convert_to_pdf(
                build_result.pptx_path, output_dir, deps.settings.pdf_export_timeout_seconds
            )
            preview_paths = render_previews(pdf_path, output_dir)
        except Exception as exc:  # noqa: BLE001 — PDF/preview export is best-effort; the native .pptx is the deliverable
            logger.warning("variant %s: pdf export failed, keeping pptx only: %s", variant, exc)

        return VariantResult(
            variant=variant,
            pptx_path=build_result.pptx_path,
            pdf_path=pdf_path,
            preview_paths=preview_paths,
            audit_report=audit_report,
        )
    except Exception as exc:
        logger.exception("variant %s failed", variant)
        return VariantResult(variant=variant, error=str(exc))
