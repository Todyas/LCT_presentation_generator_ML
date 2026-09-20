from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.agents.prompt_registry import PromptRegistry
from app.core.agents.slot_filler import SlotFillError, fill_slide
from app.models.outline import OutlineItem
from app.models.presentation_ir import BulletBlock, BulletItem, SlideIR, TitleComponent
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


def _manifest(layout_type: LayoutType) -> TemplateManifest:
    slot = LayoutSlot(
        placeholder_idx=0,
        placeholder_type=PlaceholderType.BODY,
        geometry=Geometry(left_emu=0, top_emu=0, width_emu=100, height_emu=100),
        normalized=NormalizedGeometry(x=0.0, y=0.0, w=0.5, h=0.5),
    )
    layout = LayoutManifest(
        layout_index=0,
        layout_name="Content",
        layout_type=layout_type,
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


def _outline_item(layout_type: LayoutType) -> OutlineItem:
    return OutlineItem(
        slide_index=0,
        working_title="Slide 0",
        key_message="Key message",
        suggested_layout_type=layout_type,
        content_hint="Content hint",
    )


def _valid_slide(layout_type: LayoutType) -> SlideIR:
    return SlideIR(
        slide_index=0,
        layout_type=layout_type,
        title=TitleComponent(text="A real title"),
        components=[BulletBlock(items=[BulletItem(text="one bullet")])],
    )


def _make_validation_error() -> ValidationError:
    try:
        BulletBlock(items=[BulletItem(text=f"item {i}") for i in range(7)])
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


async def test_retries_on_validation_error_then_succeeds():
    item = _outline_item(LayoutType.CONTENT_1COL)
    manifest = _manifest(LayoutType.CONTENT_1COL)
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(
        side_effect=[_make_validation_error(), _valid_slide(LayoutType.CONTENT_1COL)]
    )
    registry = PromptRegistry("skills")

    result = await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief="Brief text.",
        llm=llm,
        registry=registry,
        model="qwen",
    )

    assert isinstance(result, SlideIR)
    assert llm.complete_structured.call_count == 2
    _, second_kwargs = llm.complete_structured.call_args_list[1]
    assert "PREVIOUS ATTEMPT FAILED VALIDATION" in second_kwargs["user_prompt"]


async def test_exhausts_retries_raises_slot_fill_error():
    item = _outline_item(LayoutType.CONTENT_1COL)
    manifest = _manifest(LayoutType.CONTENT_1COL)
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(side_effect=_make_validation_error())
    registry = PromptRegistry("skills")

    with pytest.raises(SlotFillError):
        await fill_slide(
            item=item,
            variant="A",
            manifest=manifest,
            brief="Brief text.",
            llm=llm,
            registry=registry,
            model="qwen",
            max_retries=1,
        )

    assert llm.complete_structured.call_count == 2


async def test_resolved_layout_overrides_suggested_layout():
    item = _outline_item(LayoutType.TABLE_FOCUSED)
    manifest = _manifest(LayoutType.CONTENT_1COL)
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(
        return_value=_valid_slide(LayoutType.TABLE_FOCUSED)
    )
    registry = PromptRegistry("skills")

    result = await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief="Brief text.",
        llm=llm,
        registry=registry,
        model="qwen",
    )

    assert result.layout_type == LayoutType.CONTENT_1COL
