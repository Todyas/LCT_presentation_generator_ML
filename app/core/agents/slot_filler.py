from __future__ import annotations

import re

from pydantic import ValidationError

from app.core.agents.grounding import ungrounded_numbers
from app.core.agents.llm_client import LLMClient
from app.core.agents.narrative_architect import VARIANT_DESCRIPTIONS
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import OutlineItem
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ComparisonData,
    IconListData,
    MetricCard,
    ProcessData,
    SlideIR,
    TitleComponent,
)
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
    LayoutType.PROCESS_TIMELINE: ["process"],
    LayoutType.CONTENT_1COL: ["bullet_block", "icon_list"],
    LayoutType.CONTENT_2COL: ["bullet_block", "table", "chart", "comparison", "image"],
    LayoutType.COMPARISON: ["comparison", "table", "bullet_block"],
}
_DEFAULT_ALLOWED_COMPONENTS = ["bullet_block"]


def _short_source_text(value: str, max_words: int = 15) -> str:
    words = value.strip().split()
    return " ".join(words[:max_words])


def _outline_action_title(item: OutlineItem) -> str:
    candidate = item.key_message.strip()
    if len(candidate) <= 120:
        return candidate
    words: list[str] = []
    for word in candidate.split():
        if len(" ".join([*words, word])) > 119:
            break
        words.append(word)
    return " ".join(words).rstrip(".,;:") + "…"


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|;\s+|\s[—–]\s")


def _norm(value: str) -> str:
    return value.strip().strip(".,;:!?…").casefold()


def _fallback_points(key_message: str, working_title: str, title: str) -> list[str]:
    """Up to two short bullets derived from outline text, never equal to the title."""
    pieces = [p.strip() for p in _SENTENCE_SPLIT.split(key_message) if p.strip()]
    if len(pieces) < 2 and "," in key_message:
        pieces = [p.strip() for p in key_message.split(",") if p.strip()]
    candidates = [*pieces, working_title]
    points: list[str] = []
    seen = {_norm(title)}
    for candidate in candidates:
        text = _short_source_text(candidate)
        if text and _norm(text) not in seen:
            seen.add(_norm(text))
            points.append(text)
        if len(points) == 2:
            break
    return points


def build_fallback_slide(item: OutlineItem, manifest: TemplateManifest) -> SlideIR:
    """Build a source-only slide when the per-slide LLM exhausts its retries.

    `content_hint` is an internal planning note, so it is never rendered.
    """
    resolved_layout = manifest.find_layout_or_fallback(
        item.suggested_layout_type, item.slide_index
    )
    key_message = " ".join(item.key_message.split())
    sentences = [p for p in _SENTENCE_SPLIT.split(key_message) if p.strip()]
    if len(sentences) > 1 and len(sentences[0]) <= 120:
        title_text = sentences[0].strip().rstrip(".")
        rest = key_message[len(sentences[0]) :].strip()
    else:
        title_text = _outline_action_title(item.model_copy(update={"key_message": key_message}))
        rest = key_message
    points = _fallback_points(rest, item.working_title, title_text)
    if not points:
        words = key_message.split()
        half = len(words) // 2
        if half >= 2:
            points = [
                p
                for p in (
                    _short_source_text(" ".join(words[:half])),
                    _short_source_text(" ".join(words[half:])),
                )
                if _norm(p) != _norm(title_text)
            ]
    return SlideIR(
        slide_index=item.slide_index,
        layout_type=resolved_layout.layout_type,
        title=TitleComponent(text=title_text, is_action_title=True),
        components=(
            [BulletBlock(items=[BulletItem(text=point) for point in points])]
            if points
            else []
        ),
    )


def _slide_quality_problems(slide: SlideIR, requested_layout: LayoutType) -> list[str]:
    problems: list[str] = []
    bullet_blocks = [c for c in slide.components if isinstance(c, BulletBlock)]
    metric_cards = [c for c in slide.components if isinstance(c, MetricCard)]
    comparisons = [c for c in slide.components if isinstance(c, ComparisonData)]
    processes = [c for c in slide.components if isinstance(c, ProcessData)]
    icon_lists = [c for c in slide.components if isinstance(c, IconListData)]
    bullet_items = [item for block in bullet_blocks for item in block.items]
    if (
        requested_layout == LayoutType.CONTENT_1COL
        and len(bullet_items) < 2
        and not icon_lists
    ):
        problems.append("text slide needs at least two distinct supporting points")
    if (
        requested_layout == LayoutType.CONTENT_2COL
        and len(bullet_blocks) != 2
        and not any(
            component.type in {"table", "chart", "comparison", "image"}
            for component in slide.components
        )
    ):
        problems.append(
            "CONTENT_2COL requires two bullet blocks or one supported visual component"
        )
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
    if (
        requested_layout == LayoutType.COMPARISON
        and not comparisons
        and len(bullet_blocks) != 2
    ):
        problems.append(
            "COMPARISON requires a comparison component or two bullet blocks"
        )
    if requested_layout == LayoutType.PROCESS_TIMELINE and not processes:
        problems.append("PROCESS_TIMELINE requires a process component")
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
    resolved_layout = manifest.find_layout_or_fallback(
        item.suggested_layout_type, item.slide_index
    )
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
            requested_layout_type=item.suggested_layout_type.value,
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
            return slide.model_copy(
                update={
                    "slide_index": item.slide_index,
                    "layout_type": resolved_layout.layout_type,
                }
            )
        except (ValidationError, ValueError) as exc:
            last_error = exc
            retry_feedback = f"PREVIOUS ATTEMPT FAILED VALIDATION: {exc}\nFix the issue and try again."

    raise SlotFillError(item.slide_index, last_error)
