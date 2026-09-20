from unittest.mock import AsyncMock

from app.core.agents.narrative_architect import build_outline
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import Outline, OutlineItem
from app.models.template_manifest import (
    FontScheme,
    Geometry,
    LayoutManifest,
    LayoutSlot,
    LayoutType,
    NormalizedGeometry,
    PlaceholderType,
    TemplateManifest,
    ThemeColors,
)

_VALID_COLORS = {
    "dk1": "000000", "lt1": "FFFFFF", "dk2": "44546A", "lt2": "E7E6E6",
    "accent1": "4472C4", "accent2": "ED7D31", "accent3": "A5A5A5",
    "accent4": "FFC000", "accent5": "5B9BD5", "accent6": "70AD47",
    "hlink": "0563C1", "fol_hlink": "954F72",
}


def _manifest() -> TemplateManifest:
    slot = LayoutSlot(
        placeholder_idx=0,
        placeholder_type=PlaceholderType.BODY,
        geometry=Geometry(left_emu=0, top_emu=0, width_emu=100, height_emu=100),
        normalized=NormalizedGeometry(x=0.0, y=0.0, w=0.5, h=0.5),
    )
    layout = LayoutManifest(
        layout_index=0,
        layout_name="Content",
        layout_type=LayoutType.CONTENT_1COL,
        slots=[slot],
    )
    return TemplateManifest(
        source_hash="abc",
        slide_width_emu=9144000,
        slide_height_emu=6858000,
        colors=ThemeColors(**_VALID_COLORS),
        fonts=FontScheme(),
        layouts=[layout],
    )


def _outline_item(index: int) -> OutlineItem:
    return OutlineItem(
        slide_index=index,
        working_title=f"Slide {index}",
        key_message=f"Key message {index}",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint=f"Content hint {index}",
    )


async def test_build_outline_calls_llm_with_variant_description():
    outline = Outline(variant="B", items=[_outline_item(i) for i in range(10)])
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(return_value=outline)
    registry = PromptRegistry("skills")

    await build_outline(
        brief="A brief about quarterly performance.",
        manifest=_manifest(),
        variant="B",
        llm=llm,
        registry=registry,
        model="qwen",
    )

    _, kwargs = llm.complete_structured.call_args
    assert "Analytical" in kwargs["user_prompt"]
