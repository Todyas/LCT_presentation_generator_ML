import pytest
from pydantic import ValidationError

from app.models.outline import OutlineItem
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    ComparisonData,
    IconListData,
    IconListItem,
    MetricCard,
    PresentationIR,
    ProcessData,
    ProcessStep,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import LayoutType
from tests.factories import make_ir_with_all_component_types


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


def test_semantic_visual_components_validate_their_minimum_structure():
    comparison = ComparisonData(
        left_title="До",
        left_items=["Ручная работа"],
        right_title="После",
        right_items=["Единый процесс"],
    )
    process = ProcessData(
        steps=[ProcessStep(title=value) for value in ("Ввод", "Проверка", "Экспорт")]
    )
    icon_list = IconListData(
        items=[
            IconListItem(icon="speed", title="Быстрее"),
            IconListItem(icon="shield", title="Надёжнее"),
        ]
    )

    assert comparison.type == "comparison"
    assert len(process.steps) == 3
    assert icon_list.items[1].icon == "shield"


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


# --------------------------------------------------------------------------- #
# Boundary tests, both sides of every limit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("count", [1, 5])
def test_comparison_data_accepts_one_to_five_items_per_side(count):
    ComparisonData(
        left_title="До",
        left_items=[f"пункт {i}" for i in range(count)],
        right_title="После",
        right_items=[f"пункт {i}" for i in range(count)],
    )


def test_comparison_data_rejects_six_items_per_side():
    with pytest.raises(ValidationError):
        ComparisonData(
            left_title="До",
            left_items=[f"пункт {i}" for i in range(6)],
            right_title="После",
            right_items=["один пункт"],
        )


def test_comparison_data_item_rejects_more_than_fifteen_words():
    with pytest.raises(ValidationError):
        ComparisonData(
            left_title="До",
            left_items=[" ".join(["слово"] * 16)],
            right_title="После",
            right_items=["короткий пункт"],
        )


def test_comparison_data_title_accepts_sixty_chars_and_rejects_sixty_one():
    ComparisonData(
        left_title="д" * 60,
        left_items=["пункт"],
        right_title="После",
        right_items=["пункт"],
    )
    with pytest.raises(ValidationError):
        ComparisonData(
            left_title="д" * 61,
            left_items=["пункт"],
            right_title="После",
            right_items=["пункт"],
        )


@pytest.mark.parametrize("count", [3, 6])
def test_process_data_accepts_three_to_six_steps(count):
    ProcessData(steps=[ProcessStep(title=f"Шаг {i}") for i in range(count)])


@pytest.mark.parametrize("count", [2, 7])
def test_process_data_rejects_fewer_than_three_or_more_than_six_steps(count):
    with pytest.raises(ValidationError):
        ProcessData(steps=[ProcessStep(title=f"Шаг {i}") for i in range(count)])


@pytest.mark.parametrize("count", [2, 6])
def test_icon_list_data_accepts_two_to_six_items(count):
    IconListData(
        items=[IconListItem(icon="check", title=f"Пункт {i}") for i in range(count)]
    )


@pytest.mark.parametrize("count", [1, 7])
def test_icon_list_data_rejects_fewer_than_two_or_more_than_six_items(count):
    with pytest.raises(ValidationError):
        IconListData(
            items=[IconListItem(icon="check", title=f"Пункт {i}") for i in range(count)]
        )


@pytest.mark.parametrize(
    "icon", ["check", "shield", "speed", "people", "cloud", "gear", "chart", "star"]
)
def test_icon_list_item_accepts_every_allowed_icon(icon):
    IconListItem(icon=icon, title="Пункт")


def test_icon_list_item_rejects_an_unknown_icon():
    with pytest.raises(ValidationError):
        IconListItem(icon="rocket", title="Пункт")


def test_metric_card_value_accepts_twenty_chars_and_rejects_twenty_one():
    MetricCard(label="Метрика", value="в" * 20)
    with pytest.raises(ValidationError):
        MetricCard(label="Метрика", value="в" * 21)


def test_chart_data_accepts_twelve_categories_and_rejects_thirteen():
    ChartData(
        categories=[f"C{i}" for i in range(12)],
        series=[ChartSeries(name="S", values=[float(i) for i in range(12)])],
    )
    with pytest.raises(ValidationError):
        ChartData(
            categories=[f"C{i}" for i in range(13)],
            series=[ChartSeries(name="S", values=[float(i) for i in range(13)])],
        )


def test_chart_data_accepts_four_series_and_rejects_five():
    ChartData(
        categories=["C1"],
        series=[ChartSeries(name=f"S{i}", values=[1.0]) for i in range(4)],
    )
    with pytest.raises(ValidationError):
        ChartData(
            categories=["C1"],
            series=[ChartSeries(name=f"S{i}", values=[1.0]) for i in range(5)],
        )


def test_table_data_accepts_seven_columns_by_five_rows():
    TableData(
        headers=[f"H{i}" for i in range(7)],
        rows=[[f"r{r}c{c}" for c in range(7)] for r in range(5)],
    )


def test_table_data_rejects_eight_columns():
    with pytest.raises(ValidationError):
        TableData(
            headers=[f"H{i}" for i in range(8)],
            rows=[[f"r0c{c}" for c in range(8)]],
        )


def test_table_data_rejects_six_rows():
    with pytest.raises(ValidationError):
        TableData(
            headers=["H1"],
            rows=[[f"r{r}"] for r in range(6)],
        )


@pytest.mark.parametrize("count", [10, 15])
def test_presentation_ir_accepts_ten_to_fifteen_slides(count):
    slides = [_slide(i, _title(f"Slide {i}")) for i in range(count)]
    PresentationIR(variant="A", template_source_hash="abc", slides=slides)


def test_presentation_ir_rejects_sixteen_slides():
    slides = [_slide(i, _title(f"Slide {i}")) for i in range(16)]
    with pytest.raises(ValidationError):
        PresentationIR(variant="A", template_source_hash="abc", slides=slides)


def test_slide_component_union_rejects_unknown_discriminator_type():
    with pytest.raises(ValidationError):
        SlideIR(
            slide_index=0,
            layout_type=LayoutType.CONTENT_1COL,
            title=_title("Заголовок"),
            components=[{"type": "unknown_component_kind"}],
        )


def test_outline_item_empty_content_hint_falls_back_to_key_message():
    item = OutlineItem(
        slide_index=0,
        working_title="Заголовок",
        key_message="Ключевое сообщение слайда",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint="",
    )

    assert item.content_hint == item.key_message


def test_presentation_ir_round_trips_through_json_dump_for_every_component_type():
    ir = make_ir_with_all_component_types()

    round_tripped = PresentationIR.model_validate(ir.model_dump(mode="json"))

    assert round_tripped == ir
    component_types = {
        component.type for slide in ir.slides for component in slide.components
    }
    assert component_types == {
        "bullet_block",
        "metric_card",
        "chart",
        "table",
        "image",
        "comparison",
        "process",
        "icon_list",
    }
