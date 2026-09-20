from __future__ import annotations

from dataclasses import dataclass

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.shapes.base import BaseShape
from pptx.shapes.placeholder import ChartPlaceholder, TablePlaceholder
from pptx.slide import Slide
from pptx.table import Table
from pptx.text.text import TextFrame
from pptx.util import Emu, Pt

from app.core.builder.autofit import apply_autofit_to_text_frame, autofit_font_size
from app.models.presentation_ir import BulletBlock, ChartData, MetricCard, TableData
from app.models.template_manifest import Geometry, ThemeColors

_METRIC_VALUE_MAX_PT = 32
_METRIC_LABEL_MAX_PT = 14
_METRIC_CARD_GAP_EMU = 137_160  # 0.15in gap between cards in a row
_METRIC_CARD_MAX_HEIGHT_EMU = 1_600_200  # ~1.75in cap so a lone card "row" isn't absurdly tall

CHART_TYPE_MAP = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE,
    "pie": XL_CHART_TYPE.PIE,
}


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def render_bullet_block(text_frame: TextFrame, block: BulletBlock, font_path: str, font_name: str) -> None:
    text_frame.clear()
    for i, item in enumerate(block.items):
        paragraph = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
        paragraph.text = item.text
        for run in paragraph.runs:
            run.font.name = font_name


def render_bullet_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    block: BulletBlock,
    font_path: str,
    font_name: str,
) -> BBox:
    if placeholder_shape is not None:
        text_frame = placeholder_shape.text_frame
        bbox = BBox(placeholder_shape.left, placeholder_shape.top, placeholder_shape.width, placeholder_shape.height)
    else:
        textbox = slide.shapes.add_textbox(
            Emu(geometry.left_emu), Emu(geometry.top_emu), Emu(geometry.width_emu), Emu(geometry.height_emu)
        )
        text_frame = textbox.text_frame
        bbox = BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)

    render_bullet_block(text_frame, block, font_path, font_name)
    apply_autofit_to_text_frame(text_frame, geometry, font_path)
    return bbox


def render_metric_card(slide: Slide, geometry: Geometry, card: MetricCard, theme: ThemeColors, font_path: str) -> BBox:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(theme.lt2)
    shape.line.color.rgb = _rgb(theme.accent1)

    value_height = int(geometry.height_emu * 0.6)
    value_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(value_height),
    )
    value_box.text_frame.text = card.value
    value_size = autofit_font_size(
        [card.value], geometry.width_emu, value_height, font_path, max_size_pt=_METRIC_VALUE_MAX_PT
    )
    value_box.text_frame.paragraphs[0].runs[0].font.size = Pt(value_size)
    value_box.text_frame.paragraphs[0].runs[0].font.bold = True

    label_top = geometry.top_emu + value_height
    label_height = geometry.height_emu - value_height
    label_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(label_top),
        Emu(geometry.width_emu), Emu(label_height),
    )
    label_box.text_frame.text = card.label
    label_size = autofit_font_size(
        [card.label], geometry.width_emu, label_height, font_path, max_size_pt=_METRIC_LABEL_MAX_PT
    )
    label_box.text_frame.paragraphs[0].runs[0].font.size = Pt(label_size)

    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_metric_card_group(
    slide: Slide, geometry: Geometry, cards: list[MetricCard], theme: ThemeColors, font_path: str
) -> list[BBox]:
    n = len(cards)
    card_width = (geometry.width_emu - _METRIC_CARD_GAP_EMU * (n - 1)) // n
    card_height = min(geometry.height_emu, _METRIC_CARD_MAX_HEIGHT_EMU)

    boxes: list[BBox] = []
    for i, card in enumerate(cards):
        card_geometry = Geometry(
            left_emu=geometry.left_emu + i * (card_width + _METRIC_CARD_GAP_EMU),
            top_emu=geometry.top_emu,
            width_emu=card_width,
            height_emu=card_height,
        )
        boxes.append(render_metric_card(slide, card_geometry, card, theme, font_path))
    return boxes


def fill_table_cells(pptx_table: Table, table: TableData) -> None:
    for c, header in enumerate(table.headers):
        pptx_table.cell(0, c).text = header
    for r, row in enumerate(table.rows, start=1):
        for c, cell_value in enumerate(row):
            pptx_table.cell(r, c).text = cell_value


def render_table(slide: Slide, geometry: Geometry, table: TableData) -> BBox:
    rows = len(table.rows) + 1
    cols = len(table.headers)
    graphic_frame = slide.shapes.add_table(
        rows, cols,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    fill_table_cells(graphic_frame.table, table)
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_table_into_placeholder(placeholder: TablePlaceholder, table: TableData) -> BBox:
    graphic_frame = placeholder.insert_table(len(table.rows) + 1, len(table.headers))
    fill_table_cells(graphic_frame.table, table)
    return BBox(graphic_frame.left, graphic_frame.top, graphic_frame.width, graphic_frame.height)


def render_table_component(
    slide: Slide, geometry: Geometry, placeholder_shape: BaseShape | None, table: TableData
) -> BBox:
    if isinstance(placeholder_shape, TablePlaceholder):
        return render_table_into_placeholder(placeholder_shape, table)
    return render_table(slide, geometry, table)


def build_chart_data(chart: ChartData) -> CategoryChartData:
    chart_data = CategoryChartData()
    chart_data.categories = chart.categories
    for series in chart.series:
        chart_data.add_series(series.name, series.values)
    return chart_data


def render_chart(slide: Slide, geometry: Geometry, chart: ChartData) -> BBox:
    chart_data = build_chart_data(chart)
    slide.shapes.add_chart(
        CHART_TYPE_MAP[chart.chart_type],
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
        chart_data,
    )
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_chart_into_placeholder(placeholder: ChartPlaceholder, chart: ChartData) -> BBox:
    graphic_frame = placeholder.insert_chart(CHART_TYPE_MAP[chart.chart_type], build_chart_data(chart))
    return BBox(graphic_frame.left, graphic_frame.top, graphic_frame.width, graphic_frame.height)


def render_chart_component(
    slide: Slide, geometry: Geometry, placeholder_shape: BaseShape | None, chart: ChartData
) -> BBox:
    if isinstance(placeholder_shape, ChartPlaceholder):
        return render_chart_into_placeholder(placeholder_shape, chart)
    return render_chart(slide, geometry, chart)


def render_image_placeholder(slide: Slide, geometry: Geometry, alt_text: str, theme: ThemeColors) -> BBox:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb("D9D9D9")
    shape.text_frame.text = alt_text
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)
