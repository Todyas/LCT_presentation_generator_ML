from __future__ import annotations

import logging
from typing import Literal

from pydantic import ValidationError

from app.core.agents.grounding import near_duplicate_pairs, ungrounded_numbers
from app.core.agents.llm_client import LLMClient
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import Outline
from app.models.template_manifest import TemplateManifest

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
        {l.layout_type.value for l in manifest.layouts}
        | _SUPPORTED_SEMANTIC_LAYOUTS
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
            return outline
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
        return best_grounded_outline
    raise ValueError(f"outline grounding failed after retries: {last_problem}")
