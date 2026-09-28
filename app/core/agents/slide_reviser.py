from __future__ import annotations

from pydantic import ValidationError

from app.core.agents.grounding import ungrounded_numbers
from app.core.agents.llm_client import LLMClient
from app.core.agents.prompt_registry import PromptRegistry
from app.models.presentation_ir import SlideIR
from app.models.template_manifest import LayoutType, TemplateManifest


class SlideRevisionError(Exception):
    pass


async def revise_slide(
    *,
    current_slide: SlideIR,
    brief: str,
    instructions: str,
    manifest: TemplateManifest,
    llm: LLMClient,
    registry: PromptRegistry,
    model: str,
    max_retries: int = 2,
) -> SlideIR:
    """Revise one semantic slide without changing the rest of the deck."""

    spec = registry.load("slide_reviser", "latest")
    available_layout_types = sorted(
        {layout.layout_type.value for layout in manifest.layouts}
        | {
            layout_type.value
            for layout_type in LayoutType
            if layout_type not in {LayoutType.UNKNOWN, LayoutType.BLANK}
        }
    )
    feedback = ""
    last_error: Exception | None = None

    for _attempt in range(max_retries + 1):
        user_prompt = spec.user_template.format(
            brief=brief,
            current_slide_json=current_slide.model_dump_json(),
            instructions=instructions,
            available_layout_types=", ".join(available_layout_types),
            retry_feedback=feedback,
        )
        try:
            revised = await llm.complete_structured(
                model=model,
                system_prompt=spec.system_prompt,
                user_prompt=user_prompt,
                response_model=SlideIR,
                model_params=spec.model_params,
            )
            invented = sorted(ungrounded_numbers(revised, brief))
            if invented:
                raise ValueError(
                    "numeric claims absent from the brief: " + ", ".join(invented)
                )
            wants_visual = any(
                token in instructions.casefold()
                for token in ("визуаль", "график", "схем", "layout", "макет")
            )
            if wants_visual and revised.components and all(
                component.type == "bullet_block" for component in revised.components
            ):
                raise ValueError(
                    "visual revision requested but response contains only bullet text"
                )
            # A slide-level edit must never move or renumber the slide.
            return revised.model_copy(update={"slide_index": current_slide.slide_index})
        except (ValidationError, ValueError) as exc:
            last_error = exc
            feedback = (
                f"PREVIOUS ATTEMPT FAILED VALIDATION: {exc}\nFix it and try again."
            )

    raise SlideRevisionError(
        f"failed to revise slide {current_slide.slide_index}: {last_error}"
    )
