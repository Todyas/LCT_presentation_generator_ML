from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

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
    purposes = [
        "context",
        "audience",
        "problem",
        "cause",
        "approach",
        "process",
        "architecture",
        "benefit",
        "risk",
        "decision",
    ]
    layouts = {
        2: LayoutType.CONTENT_2COL,
        4: LayoutType.COMPARISON,
        5: LayoutType.PROCESS_TIMELINE,
        8: LayoutType.CONTENT_2COL,
    }
    return OutlineItem(
        slide_index=index,
        working_title=f"Slide {index}",
        key_message=f"Distinct {purposes[index]} message",
        suggested_layout_type=layouts.get(index, LayoutType.CONTENT_1COL),
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
    assert "KPI_DASHBOARD" in kwargs["user_prompt"]


async def test_build_outline_retries_schema_validation_errors():
    with pytest.raises(ValidationError) as captured:
        Outline.model_validate(
            {
                "variant": "A",
                "items": [
                    {
                        **_outline_item(i).model_dump(),
                        "working_title": "" if i == 0 else f"Slide {i}",
                    }
                    for i in range(10)
                ],
            }
        )
    valid = Outline(variant="A", items=[_outline_item(i) for i in range(10)])
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(side_effect=[captured.value, valid])

    result = await build_outline(
        brief="A brief about quarterly performance.",
        manifest=_manifest(),
        variant="A",
        llm=llm,
        registry=PromptRegistry("skills"),
        model="qwen",
    )

    assert result == valid
    assert llm.complete_structured.await_count == 2


async def test_build_outline_accepts_best_grounded_result_after_duplicate_retries():
    items = [_outline_item(i) for i in range(10)]
    items[4] = items[4].model_copy(update={"key_message": items[0].key_message})
    repeated = Outline(variant="C", items=items)
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(return_value=repeated)

    result = await build_outline(
        brief="A brief about quarterly performance.",
        manifest=_manifest(),
        variant="C",
        llm=llm,
        registry=PromptRegistry("skills"),
        model="qwen",
    )

    assert result == repeated
    assert llm.complete_structured.await_count == 3


def test_empty_content_hint_falls_back_to_key_message():
    item = _outline_item(0).model_copy(update={"content_hint": ""})
    reparsed = OutlineItem.model_validate(item.model_dump())

    assert reparsed.content_hint == reparsed.key_message
