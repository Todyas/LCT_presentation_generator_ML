from __future__ import annotations

from typing import Literal

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
    available_layout_types = sorted({l.layout_type.value for l in manifest.layouts})
    base_user_prompt = spec.user_template.format(
        brief=brief,
        variant_description=VARIANT_DESCRIPTIONS[variant],
        n_slides_min=n_slides_min,
        n_slides_max=n_slides_max,
        available_layout_types=", ".join(available_layout_types),
    )
    feedback = ""
    last_problem = ""
    for _attempt in range(3):
        outline = await llm.complete_structured(
            model=model,
            system_prompt=spec.system_prompt,
            user_prompt=base_user_prompt + feedback,
            response_model=Outline,
            model_params=spec.model_params,
        )
        invented = sorted(ungrounded_numbers(outline, brief))
        duplicates = near_duplicate_pairs(item.key_message for item in outline.items)
        problems: list[str] = []
        if invented:
            problems.append(f"numbers absent from BRIEF: {', '.join(invented)}")
        if duplicates:
            pairs = ", ".join(f"{a + 1}/{b + 1}" for a, b in duplicates[:5])
            problems.append(f"near-duplicate slide messages at positions: {pairs}")
        if not problems:
            return outline
        last_problem = "; ".join(problems)
        feedback = (
            "\n\nPREVIOUS OUTLINE WAS REJECTED: "
            f"{last_problem}. Rebuild the outline using only source facts and "
            "give every slide a distinct communicative purpose."
        )
    raise ValueError(f"outline grounding failed after retries: {last_problem}")
