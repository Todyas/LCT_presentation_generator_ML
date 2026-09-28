from __future__ import annotations

from pydantic import ValidationError

from app.models.presentation_ir import (
    BulletBlock,
    ComparisonData,
    IconListData,
    IconListItem,
    PresentationIR,
    ProcessData,
    ProcessStep,
    SlideIR,
)
from app.models.template_manifest import LayoutType

_ICONS = ("check", "shield", "speed", "people", "cloud", "gear")


def _comparison_from_bullets(slide: SlideIR) -> SlideIR | None:
    blocks = [item for item in slide.components if isinstance(item, BulletBlock)]
    if len(blocks) != 2:
        return None
    component = ComparisonData(
        left_title="До",
        left_items=[item.text for item in blocks[0].items],
        right_title="После",
        right_items=[item.text for item in blocks[1].items],
    )
    return slide.model_copy(update={"components": [component]})


def _process_from_bullets(slide: SlideIR) -> SlideIR | None:
    blocks = [item for item in slide.components if isinstance(item, BulletBlock)]
    points = [item.text for block in blocks for item in block.items]
    if len(points) < 3:
        return None
    steps = []
    try:
        for point in points[:6]:
            label, separator, description = point.partition(":")
            steps.append(
                ProcessStep(
                    title=label.strip(" *") if separator else point.strip(" *"),
                    description=description.strip() if separator else "",
                )
            )
    except ValidationError:
        # Bullets allow longer text than step titles; keep the slide as bullets.
        return None
    return slide.model_copy(update={"components": [ProcessData(steps=steps)]})


def _icon_list_from_bullets(slide: SlideIR) -> SlideIR | None:
    blocks = [item for item in slide.components if isinstance(item, BulletBlock)]
    if len(blocks) != 1 or len(blocks[0].items) < 2:
        return None
    items = []
    for index, point in enumerate(blocks[0].items):
        title, separator, description = point.text.partition(":")
        title = title.strip(" *") if separator else point.text.strip(" *")
        description = description.strip() if separator else ""
        if len(title) > 60:
            title, overflow = title[:57].rstrip() + "...", title[57:]
            description = (overflow + " " + description).strip() if description else overflow
        if len(description) > 140:
            description = description[:137].rstrip() + "..."
        items.append(
            IconListItem(
                icon=_ICONS[index % len(_ICONS)],
                title=title,
                description=description,
            )
        )
    return slide.model_copy(update={"components": [IconListData(items=items)]})


def apply_visual_policy(ir: PresentationIR) -> PresentationIR:
    """Diversify safe text-only slides without inventing any new facts."""

    result: list[SlideIR] = []
    text_only_streak = 0
    for slide in ir.slides:
        converted: SlideIR | None = None
        if slide.layout_type == LayoutType.COMPARISON:
            converted = _comparison_from_bullets(slide)
        elif slide.layout_type == LayoutType.PROCESS_TIMELINE:
            converted = _process_from_bullets(slide)

        candidate = converted or slide
        bullet_only = bool(candidate.components) and all(
            isinstance(component, BulletBlock) for component in candidate.components
        )
        text_only_streak = text_only_streak + 1 if bullet_only else 0
        if text_only_streak > 2:
            converted = _icon_list_from_bullets(candidate)
            if converted is not None:
                candidate = converted
                text_only_streak = 0
        result.append(candidate)

    return ir.model_copy(update={"slides": result})
