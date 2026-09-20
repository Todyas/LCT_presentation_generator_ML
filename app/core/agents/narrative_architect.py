from __future__ import annotations

from typing import Literal

from app.core.agents.llm_client import LLMClient
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import Outline
from app.models.template_manifest import TemplateManifest

VARIANT_DESCRIPTIONS: dict[str, str] = {
    "A": "Executive: big KPIs and thesis statements. Minimal bullets. Each slide carries one key conclusion.",
    "B": "Analytical: prioritize native charts (ChartData) and comparison tables (TableData) over text.",
    "C": "Structural/Process: prioritize step-by-step cards, timelines, and structured lists (LayoutType.PROCESS_TIMELINE).",
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
    user_prompt = spec.user_template.format(
        brief=brief,
        variant_description=VARIANT_DESCRIPTIONS[variant],
        n_slides_min=n_slides_min,
        n_slides_max=n_slides_max,
        available_layout_types=", ".join(available_layout_types),
    )
    outline = await llm.complete_structured(
        model=model,
        system_prompt=spec.system_prompt,
        user_prompt=user_prompt,
        response_model=Outline,
        model_params=spec.model_params,
    )
    return outline
