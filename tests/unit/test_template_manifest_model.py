import pytest
from pydantic import ValidationError

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


def test_theme_colors_rejects_invalid_hex():
    bad_colors = dict(_VALID_COLORS, dk1="not-a-color")
    with pytest.raises(ValidationError):
        ThemeColors(**bad_colors)


def _make_slot(placeholder_type: PlaceholderType) -> LayoutSlot:
    return LayoutSlot(
        placeholder_idx=0,
        placeholder_type=placeholder_type,
        geometry=Geometry(left_emu=0, top_emu=0, width_emu=100, height_emu=100),
        normalized=NormalizedGeometry(x=0.0, y=0.0, w=0.5, h=0.5),
    )


def test_find_layout_or_fallback_returns_layouts_zero_when_no_body_slot():
    unknown_layout = LayoutManifest(
        layout_index=0,
        layout_name="Custom",
        layout_type=LayoutType.UNKNOWN,
        slots=[],
    )
    title_layout = LayoutManifest(
        layout_index=1,
        layout_name="Title",
        layout_type=LayoutType.TITLE_SLIDE,
        slots=[_make_slot(PlaceholderType.TITLE)],
    )
    manifest = TemplateManifest(
        source_hash="abc",
        slide_width_emu=9144000,
        slide_height_emu=6858000,
        colors=ThemeColors(**_VALID_COLORS),
        fonts=FontScheme(),
        layouts=[unknown_layout, title_layout],
    )

    result = manifest.find_layout_or_fallback(LayoutType.CONTENT_1COL)

    assert result is manifest.layouts[0]
