from __future__ import annotations

from pydantic import ValidationError

from app.core.agents.grounding import ungrounded_numbers
from app.core.agents.llm_client import LLMClient
from app.core.agents.narrative_architect import VARIANT_DESCRIPTIONS
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import OutlineItem
from app.models.presentation_ir import BulletBlock, MetricCard, SlideIR
from app.models.template_manifest import LayoutType, TemplateManifest


class SlotFillError(Exception):
    def __init__(self, slide_index: int, last_error: Exception) -> None:
        super().__init__(f"failed to fill slide {slide_index}: {last_error}")
        self.slide_index = slide_index
        self.last_error = last_error


_ALLOWED_COMPONENTS_BY_LAYOUT: dict[LayoutType, list[str]] = {
    LayoutType.TABLE_FOCUSED: ["table"],
    LayoutType.CHART_FOCUSED: ["chart"],
    LayoutType.KPI_DASHBOARD: ["metric_card"],
    LayoutType.PROCESS_TIMELINE: ["bullet_block", "metric_card"],
    # ImagePlaceholder is deliberately not offered yet: until the asset pipeline
    # can resolve it to a real image, it would render as an empty grey rectangle.
    LayoutType.CONTENT_1COL: ["bullet_block"],
    LayoutType.CONTENT_2COL: ["bullet_block", "table", "chart"],
    LayoutType.COMPARISON: ["table", "bullet_block"],
}
_DEFAULT_ALLOWED_COMPONENTS = ["bullet_block"]


def _slide_quality_problems(slide: SlideIR, requested_layout: LayoutType) -> list[str]:
    problems: list[str] = []
    bullet_blocks = [c for c in slide.components if isinstance(c, BulletBlock)]
    metric_cards = [c for c in slide.components if isinstance(c, MetricCard)]
    bullet_items = [item for block in bullet_blocks for item in block.items]
    if requested_layout in {LayoutType.CONTENT_1COL, LayoutType.COMPARISON} and len(
        bullet_items
    ) < 2:
        problems.append("text slide needs at least two distinct supporting points")
    if requested_layout == LayoutType.CONTENT_2COL and len(bullet_blocks) != 2:
        problems.append("CONTENT_2COL requires exactly two bullet_block components")
    if requested_layout == LayoutType.KPI_DASHBOARD and len(metric_cards) < 3:
        problems.append("KPI_DASHBOARD requires at least three metric_card components")
    if requested_layout == LayoutType.CHART_FOCUSED and not any(
        component.type == "chart" for component in slide.components
    ):
        problems.append("CHART_FOCUSED requires a chart component")
    if requested_layout == LayoutType.TABLE_FOCUSED and not any(
        component.type == "table" for component in slide.components
    ):
        problems.append("TABLE_FOCUSED requires a table component")
    if any(item.text.lstrip("*").lower().startswith("тезис:") for item in bullet_items):
        problems.append("generic 'Тезис:' labels are forbidden")
    return problems


async def fill_slide(
    item: OutlineItem,
    variant: str,
    manifest: TemplateManifest,
    brief: str,
    llm: LLMClient,
    registry: PromptRegistry,
    model: str,
    max_retries: int = 2,
) -> SlideIR:
    resolved_layout = manifest.find_layout_or_fallback(item.suggested_layout_type, item.slide_index)
    # Composition is a semantic decision. A generic BODY placeholder can host
    # a native chart/table/card group, so a weak template classification must
    # not collapse every requested archetype back to bullet text.
    allowed_components = _ALLOWED_COMPONENTS_BY_LAYOUT.get(
        item.suggested_layout_type, _DEFAULT_ALLOWED_COMPONENTS
    )
    spec = registry.load("slot_filler", "latest")

    retry_feedback = ""
    last_error: Exception | None = None
    for _attempt in range(max_retries + 1):
        user_prompt = spec.user_template.format(
            brief=brief,
            variant_description=VARIANT_DESCRIPTIONS[variant],
            slide_index=item.slide_index,
            working_title=item.working_title,
            key_message=item.key_message,
            content_hint=item.content_hint,
            resolved_layout_type=resolved_layout.layout_type.value,
            allowed_component_types=", ".join(allowed_components),
            retry_feedback=retry_feedback,
        )
        try:
            slide = await llm.complete_structured(
                model=model,
                system_prompt=spec.system_prompt,
                user_prompt=user_prompt,
                response_model=SlideIR,
                model_params=spec.model_params,
            )
            invented = sorted(ungrounded_numbers(slide, brief))
            if invented:
                raise ValueError(
                    "numeric claims absent from the brief: " + ", ".join(invented)
                )
            quality_problems = _slide_quality_problems(
                slide, item.suggested_layout_type
            )
            if quality_problems:
                raise ValueError("; ".join(quality_problems))
            return slide.model_copy(update={"layout_type": resolved_layout.layout_type})
        except (ValidationError, ValueError) as exc:
            last_error = exc
            retry_feedback = f"PREVIOUS ATTEMPT FAILED VALIDATION: {exc}\nFix the issue and try again."

    raise SlotFillError(item.slide_index, last_error)
