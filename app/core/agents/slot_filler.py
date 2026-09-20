from __future__ import annotations

from pydantic import ValidationError

from app.core.agents.llm_client import LLMClient
from app.core.agents.narrative_architect import VARIANT_DESCRIPTIONS
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import OutlineItem
from app.models.presentation_ir import SlideIR
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
    LayoutType.CONTENT_1COL: ["bullet_block", "image"],
    LayoutType.CONTENT_2COL: ["bullet_block", "image", "table", "chart"],
    LayoutType.COMPARISON: ["table", "bullet_block"],
}
_DEFAULT_ALLOWED_COMPONENTS = ["bullet_block"]


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
    allowed_components = _ALLOWED_COMPONENTS_BY_LAYOUT.get(
        resolved_layout.layout_type, _DEFAULT_ALLOWED_COMPONENTS
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
            return slide.model_copy(update={"layout_type": resolved_layout.layout_type})
        except ValidationError as exc:
            last_error = exc
            retry_feedback = f"PREVIOUS ATTEMPT FAILED VALIDATION: {exc}\nFix the issue and try again."

    raise SlotFillError(item.slide_index, last_error)
