from app.core.parser.layout_classifier import classify_layout
from app.models.template_manifest import (
    Geometry,
    LayoutSlot,
    LayoutType,
    NormalizedGeometry,
    PlaceholderType,
)


def _slot(placeholder_type: PlaceholderType, x: float, w: float) -> LayoutSlot:
    return LayoutSlot(
        placeholder_idx=0,
        placeholder_type=placeholder_type,
        geometry=Geometry(left_emu=0, top_emu=0, width_emu=100, height_emu=100),
        normalized=NormalizedGeometry(x=x, y=0.1, w=w, h=0.2),
    )


def test_name_wins_over_geometry():
    slots = [_slot(PlaceholderType.BODY, 0.1, 0.8)]

    result = classify_layout("Title Slide", slots)

    assert result == LayoutType.TITLE_SLIDE


def test_title_and_content_is_not_misclassified_as_cover():
    slots = [
        _slot(PlaceholderType.TITLE, 0.05, 0.9),
        _slot(PlaceholderType.BODY, 0.05, 0.9),
    ]

    result = classify_layout("Title and Content", slots)

    assert result == LayoutType.CONTENT_1COL


def test_uninformative_name_falls_back_to_geometry_two_col():
    slots = [
        _slot(PlaceholderType.TITLE, 0.05, 0.9),
        _slot(PlaceholderType.BODY, 0.05, 0.40),
        _slot(PlaceholderType.BODY, 0.55, 0.40),
    ]

    result = classify_layout("Custom 7", slots)

    assert result == LayoutType.CONTENT_2COL


def test_empty_name_and_no_slots_is_blank():
    result = classify_layout("", [])

    assert result == LayoutType.BLANK


def test_single_body_slot_is_content_1col():
    slots = [
        _slot(PlaceholderType.TITLE, 0.05, 0.9),
        _slot(PlaceholderType.BODY, 0.05, 0.9),
    ]

    result = classify_layout("Content", slots)

    assert result == LayoutType.CONTENT_1COL
