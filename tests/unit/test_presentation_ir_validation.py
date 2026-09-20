import pytest
from pydantic import ValidationError

from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    PresentationIR,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import LayoutType


def _title(text: str) -> TitleComponent:
    return TitleComponent(text=text)


def _slide(index: int, title: TitleComponent) -> SlideIR:
    return SlideIR(
        slide_index=index,
        layout_type=LayoutType.CONTENT_1COL,
        title=title,
        components=[],
    )


def test_bullet_block_rejects_more_than_six_items():
    with pytest.raises(ValidationError):
        BulletBlock(items=[BulletItem(text=f"item {i}") for i in range(7)])


def test_bullet_item_rejects_more_than_fifteen_words():
    with pytest.raises(ValidationError):
        BulletItem(
            text=(
                "one two three four five six seven eight nine ten eleven "
                "twelve thirteen fourteen fifteen sixteen"
            )
        )


def test_chart_data_rejects_series_length_mismatch():
    with pytest.raises(ValidationError, match="revenue"):
        ChartData(
            categories=["Q1", "Q2", "Q3"],
            series=[ChartSeries(name="revenue", values=[1.0, 2.0])],
        )


def test_table_data_rejects_row_with_wrong_cell_count():
    with pytest.raises(ValidationError):
        TableData(headers=["a", "b", "c"], rows=[["1", "2"]])


def test_slide_ir_rejects_placeholder_title_case_insensitive():
    with pytest.raises(ValidationError):
        _slide(0, _title("TODO fix this slide"))


def test_presentation_ir_rejects_duplicate_slide_indices():
    slides = [_slide(0, _title(f"Slide {i}")) for i in range(10)]
    slides[1] = _slide(0, _title("Duplicate index"))
    with pytest.raises(ValidationError):
        PresentationIR(variant="A", template_source_hash="abc", slides=slides)


def test_presentation_ir_rejects_fewer_than_ten_slides():
    slides = [_slide(i, _title(f"Slide {i}")) for i in range(9)]
    with pytest.raises(ValidationError):
        PresentationIR(variant="A", template_source_hash="abc", slides=slides)
