from __future__ import annotations

import re
from dataclasses import dataclass

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.shapes.base import BaseShape
from pptx.shapes.placeholder import ChartPlaceholder, TablePlaceholder
from pptx.slide import Slide
from pptx.table import Table
from pptx.text.text import TextFrame
from pptx.util import Emu, Pt

from app.core.builder.autofit import apply_autofit_to_text_frame, autofit_font_size
from app.models.presentation_ir import BulletBlock, ChartData, MetricCard, TableData
from app.models.template_manifest import Geometry, ThemeColors

_METRIC_VALUE_MAX_PT = 28
_METRIC_LABEL_MAX_PT = 14
_METRIC_TEXT_MARGIN_PT = 4  # tight inner margin so long values like "2,4 млрд ₽" keep their width
_METRIC_CARD_GAP_EMU = 317_500  # 25pt gap between cards in a row, wide enough to stop edge clipping
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


_BOLD_MARKDOWN = re.compile(r"\*\*(.+?)\*\*")


def _render_bullet_text(paragraph, text: str) -> None:
    pos = 0
    for match in _BOLD_MARKDOWN.finditer(text):
        if match.start() > pos:
            paragraph.add_run().text = text[pos : match.start()]
        bold_run = paragraph.add_run()
        bold_run.text = match.group(1)
        bold_run.font.bold = True
        pos = match.end()
    if pos < len(text) or not paragraph.runs:
        paragraph.add_run().text = text[pos:]


def render_bullet_block(text_frame: TextFrame, block: BulletBlock, font_path: str, font_name: str) -> None:
    text_frame.clear()
    for i, item in enumerate(block.items):
        paragraph = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
        _render_bullet_text(paragraph, item.text)
        for run in paragraph.runs:
            run.font.name = font_name


def render_bullet_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    block: BulletBlock,
    font_path: str,
    font_name: str,
    theme: ThemeColors,
) -> BBox:
    if placeholder_shape is not None:
        text_frame = placeholder_shape.text_frame
        panel = placeholder_shape
        bbox = BBox(placeholder_shape.left, placeholder_shape.top, placeholder_shape.width, placeholder_shape.height)
    else:
        panel = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Emu(geometry.left_emu), Emu(geometry.top_emu), Emu(geometry.width_emu), Emu(geometry.height_emu)
        )
        text_frame = panel.text_frame
        bbox = BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)

    render_bullet_block(text_frame, block, font_path, font_name)
    panel.fill.solid()
    panel.fill.fore_color.rgb = _rgb(theme.lt2)
    panel.line.color.rgb = _rgb(theme.accent1)
    panel.line.width = Pt(1.5)
    text_frame.margin_left = Pt(14)
    text_frame.margin_right = Pt(14)
    text_frame.margin_top = Pt(10)
    text_frame.margin_bottom = Pt(10)
    text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    for paragraph in text_frame.paragraphs:
        paragraph.space_after = Pt(8)
        paragraph.line_spacing = 1.05
        for run in paragraph.runs:
            run.font.color.rgb = _rgb(theme.dk1)
    apply_autofit_to_text_frame(text_frame, geometry, font_path, max_size_pt=20, line_spacing=1.25)
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

    margin_emu = int(Pt(_METRIC_TEXT_MARGIN_PT))
    usable_width = geometry.width_emu - 2 * margin_emu

    value_height = int(geometry.height_emu * 0.6)
    value_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(value_height),
    )
    value_box.text_frame.margin_left = Pt(_METRIC_TEXT_MARGIN_PT)
    value_box.text_frame.margin_right = Pt(_METRIC_TEXT_MARGIN_PT)
    value_box.text_frame.text = card.value
    value_size = autofit_font_size(
        [card.value], usable_width, value_height, font_path, max_size_pt=_METRIC_VALUE_MAX_PT
    )
    value_box.text_frame.paragraphs[0].runs[0].font.size = Pt(value_size)
    value_box.text_frame.paragraphs[0].runs[0].font.bold = True

    label_top = geometry.top_emu + value_height
    label_height = geometry.height_emu - value_height
    label_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(label_top),
        Emu(geometry.width_emu), Emu(label_height),
    )
    label_box.text_frame.margin_left = Pt(_METRIC_TEXT_MARGIN_PT)
    label_box.text_frame.margin_right = Pt(_METRIC_TEXT_MARGIN_PT)
    label = card.label if not card.delta else f"{card.label} · {card.delta}"
    label_box.text_frame.text = label
    label_size = autofit_font_size(
        [label], usable_width, label_height, font_path, max_size_pt=_METRIC_LABEL_MAX_PT
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


def fill_table_cells(
    pptx_table: Table, table: TableData, theme: ThemeColors | None = None
) -> None:
    for c, header in enumerate(table.headers):
        pptx_table.cell(0, c).text = header
    for r, row in enumerate(table.rows, start=1):
        for c, cell_value in enumerate(row):
            pptx_table.cell(r, c).text = cell_value
    if theme is None:
        return
    for c in range(len(table.headers)):
        cell = pptx_table.cell(0, c)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(theme.accent1)
        for paragraph in cell.text_frame.paragraphs:
            paragraph.alignment = PP_ALIGN.CENTER
            for run in paragraph.runs:
                run.font.bold = True
                run.font.color.rgb = _rgb(theme.lt1)
    for r in range(1, len(table.rows) + 1):
        for c in range(len(table.headers)):
            cell = pptx_table.cell(r, c)
            if r % 2 == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(theme.lt2)
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = _rgb(theme.dk1)


def render_table(
    slide: Slide,
    geometry: Geometry,
    table: TableData,
    theme: ThemeColors | None = None,
) -> BBox:
    rows = len(table.rows) + 1
    cols = len(table.headers)
    graphic_frame = slide.shapes.add_table(
        rows, cols,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    fill_table_cells(graphic_frame.table, table, theme)
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_table_into_placeholder(
    placeholder: TablePlaceholder,
    table: TableData,
    theme: ThemeColors | None = None,
) -> BBox:
    graphic_frame = placeholder.insert_table(len(table.rows) + 1, len(table.headers))
    fill_table_cells(graphic_frame.table, table, theme)
    return BBox(graphic_frame.left, graphic_frame.top, graphic_frame.width, graphic_frame.height)


def render_table_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    table: TableData,
    theme: ThemeColors,
) -> BBox:
    if isinstance(placeholder_shape, TablePlaceholder):
        return render_table_into_placeholder(placeholder_shape, table, theme)
    return render_table(slide, geometry, table, theme)


def build_chart_data(chart: ChartData) -> CategoryChartData:
    chart_data = CategoryChartData()
    chart_data.categories = chart.categories
    for series in chart.series:
        chart_data.add_series(series.name, series.values)
    return chart_data


def render_chart(slide: Slide, geometry: Geometry, chart: ChartData) -> BBox:
    chart_data = build_chart_data(chart)
    graphic_frame = slide.shapes.add_chart(
        CHART_TYPE_MAP[chart.chart_type],
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
        chart_data,
    )
    graphic_frame.chart.chart_style = 10
    graphic_frame.chart.has_legend = len(chart.series) > 1
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_chart_into_placeholder(placeholder: ChartPlaceholder, chart: ChartData) -> BBox:
    graphic_frame = placeholder.insert_chart(CHART_TYPE_MAP[chart.chart_type], build_chart_data(chart))
    graphic_frame.chart.chart_style = 10
    graphic_frame.chart.has_legend = len(chart.series) > 1
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
