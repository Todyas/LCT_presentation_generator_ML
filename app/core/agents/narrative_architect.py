from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import ValidationError

from app.core.agents.grounding import near_duplicate_pairs, ungrounded_numbers
from app.core.agents.llm_client import LLMClient
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import Outline, OutlineItem
from app.models.template_manifest import LayoutType, TemplateManifest

VARIANT_DESCRIPTIONS: dict[str, str] = {
    "A": "Executive: decision-oriented story with strong conclusions, evidence cards, comparisons and KPI blocks when the brief contains metrics. Concise does not mean empty: every slide needs supporting evidence.",
    "B": "Analytical: expose reasoning and evidence; use native charts and comparison tables only when the brief contains grounded data, otherwise use structured comparisons and process views.",
    "C": "Pitch: strong problem-to-solution storytelling for a large screen, varied visual rhythm, memorable messages and a concrete call to action.",
}

logger = logging.getLogger(__name__)

_SUPPORTED_SEMANTIC_LAYOUTS = {
    "TITLE_SLIDE",
    "SECTION_HEADER",
    "CONTENT_1COL",
    "CONTENT_2COL",
    "COMPARISON",
    "TABLE_FOCUSED",
    "CHART_FOCUSED",
    "KPI_DASHBOARD",
    "PROCESS_TIMELINE",
    "QUOTE",
}


def _visual_structure_problems(outline: Outline) -> list[str]:
    layout_types = [item.suggested_layout_type.value for item in outline.items]
    problems: list[str] = []
    if len(set(layout_types)) < 3:
        problems.append("fewer than three distinct slide compositions")
    if "COMPARISON" not in layout_types:
        problems.append("missing a qualitative comparison slide")
    if "PROCESS_TIMELINE" not in layout_types:
        problems.append("missing a process or timeline slide")
    for index in range(len(layout_types) - 2):
        if layout_types[index : index + 3] == ["CONTENT_1COL"] * 3:
            problems.append("three consecutive CONTENT_1COL slides")
            break
    return problems


def _fit_outline_count(outline: Outline, brief: str, target: int) -> Outline:
    """Return exactly *target* grounded, sequential outline items.

    Providers occasionally ignore an exact 12–12 request and return 10 or 11
    otherwise valid items.  Count is a product invariant, so do not let that
    turn into an empty variant later in the pipeline.
    """
    items = [item.model_copy(deep=True) for item in outline.items[:target]]
    fragments = [
        value.strip(" \t\n-•")
        for value in re.split(r"(?:\n+|(?<=[.!?])\s+)", brief)
        if len(value.strip(" \t\n-•")) >= 24
    ]
    fallback_titles = (
        "Контекст и предпосылки",
        "Практическое применение",
        "Ожидаемый результат",
        "Условия внедрения",
        "Следующие действия",
    )
    existing = " ".join(item.key_message.casefold() for item in items)
    candidates = [
        fragment for fragment in fragments if fragment.casefold() not in existing
    ]
    if not candidates:
        candidates = fragments or [brief.strip()]
    while len(items) < target:
        offset = len(items)
        message = candidates[offset % len(candidates)].strip()
        if len(message) > 300:
            message = message[:297].rsplit(" ", 1)[0] + "…"
        title = fallback_titles[offset % len(fallback_titles)]
        items.append(
            OutlineItem(
                slide_index=offset,
                working_title=title,
                key_message=message,
                suggested_layout_type=(
                    LayoutType.CONTENT_2COL if offset % 2 else LayoutType.CONTENT_1COL
                ),
                content_hint=message,
            )
        )
    for index, item in enumerate(items):
        item.slide_index = index
    return Outline(variant=outline.variant, items=items)


async def build_outline(
    brief: str,
    manifest: TemplateManifest,
    variant: Literal["A", "B", "C"],
    llm: LLMClient,
    registry: PromptRegistry,
    model: str,
    n_slides_min: int = 10,
    n_slides_max: int = 15,
) -> Outline:
    spec = registry.load("narrative_architect", "latest")
    # Cards, charts and tables are rendered natively into a generic content
    # region. They stay available even when a corporate template calls every
    # custom layout UNKNOWN.
    available_layout_types = sorted(
        {l.layout_type.value for l in manifest.layouts} | _SUPPORTED_SEMANTIC_LAYOUTS
    )
    base_user_prompt = spec.user_template.format(
        brief=brief,
        variant_description=VARIANT_DESCRIPTIONS[variant],
        n_slides_min=n_slides_min,
        n_slides_max=n_slides_max,
        available_layout_types=", ".join(available_layout_types),
    )
    feedback = ""
    last_problem = ""
    best_grounded_outline: Outline | None = None
    best_duplicate_count: int | None = None
    for _attempt in range(3):
        try:
            outline = await llm.complete_structured(
                model=model,
                system_prompt=spec.system_prompt,
                user_prompt=base_user_prompt + feedback,
                response_model=Outline,
                model_params=spec.model_params,
            )
        except ValidationError as exc:
            first_error = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first_error.get("loc", ()))
            last_problem = f"invalid outline field {location or 'unknown'}"
            feedback = (
                "\n\nPREVIOUS OUTLINE FAILED JSON SCHEMA VALIDATION: "
                f"{last_problem}. Return every required field with non-empty "
                "working_title and key_message; content_hint must describe the "
                "specific evidence or composition for that slide."
            )
            continue
        invented = sorted(ungrounded_numbers(outline, brief))
        duplicates = near_duplicate_pairs(item.key_message for item in outline.items)
        problems: list[str] = []
        if n_slides_min == n_slides_max and len(outline.items) != n_slides_min:
            problems.append(
                f"expected exactly {n_slides_min} slides, got {len(outline.items)}"
            )
        if invented:
            problems.append(f"numbers absent from BRIEF: {', '.join(invented)}")
        if duplicates:
            pairs = ", ".join(f"{a + 1}/{b + 1}" for a, b in duplicates[:5])
            problems.append(f"near-duplicate slide messages at positions: {pairs}")
        problems.extend(_visual_structure_problems(outline))
        if not invented and (
            best_duplicate_count is None or len(duplicates) < best_duplicate_count
        ):
            best_grounded_outline = outline
            best_duplicate_count = len(duplicates)
        if not problems:
            target = max(n_slides_min, min(n_slides_max, len(outline.items)))
            return _fit_outline_count(outline, brief, target)
        last_problem = "; ".join(problems)
        feedback = (
            "\n\nPREVIOUS OUTLINE WAS REJECTED: "
            f"{last_problem}. Rebuild the outline using only source facts and "
            "give every slide a distinct communicative purpose."
        )
    if best_grounded_outline is not None:
        logger.warning(
            "accepting grounded outline with %s near-duplicate pair(s) after retries",
            best_duplicate_count,
        )
        target = (
            n_slides_min
            if n_slides_min == n_slides_max
            else max(n_slides_min, min(n_slides_max, len(best_grounded_outline.items)))
        )
        return _fit_outline_count(best_grounded_outline, brief, target)
    raise ValueError(f"outline grounding failed after retries: {last_problem}")
