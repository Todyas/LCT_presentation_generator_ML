from __future__ import annotations

from dataclasses import dataclass

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.slide import Slide
from pptx.text.text import TextFrame
from pptx.util import Emu, Pt

from app.models.presentation_ir import BulletBlock, ChartData, MetricCard, TableData
from app.models.template_manifest import Geometry, ThemeColors


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


def render_metric_card(slide: Slide, geometry: Geometry, card: MetricCard, theme: ThemeColors) -> BBox:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(theme.lt2)
    shape.line.color.rgb = _rgb(theme.accent1)

    value_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(int(geometry.height_emu * 0.6)),
    )
    value_box.text_frame.text = card.value
    value_box.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    value_box.text_frame.paragraphs[0].runs[0].font.bold = True

    label_box = slide.shapes.add_textbox(
        Emu(geometry.left_emu), Emu(geometry.top_emu + int(geometry.height_emu * 0.6)),
        Emu(geometry.width_emu), Emu(int(geometry.height_emu * 0.4)),
    )
    label_box.text_frame.text = card.label
    label_box.text_frame.paragraphs[0].runs[0].font.size = Pt(14)

    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_table(slide: Slide, geometry: Geometry, table: TableData) -> BBox:
    rows = len(table.rows) + 1
    cols = len(table.headers)
    graphic_frame = slide.shapes.add_table(
        rows, cols,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    pptx_table = graphic_frame.table
    for c, header in enumerate(table.headers):
        pptx_table.cell(0, c).text = header
    for r, row in enumerate(table.rows, start=1):
        for c, cell_value in enumerate(row):
            pptx_table.cell(r, c).text = cell_value
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


_CHART_TYPE_MAP = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE,
    "pie": XL_CHART_TYPE.PIE,
}


def render_chart(slide: Slide, geometry: Geometry, chart: ChartData) -> BBox:
    chart_data = CategoryChartData()
    chart_data.categories = chart.categories
    for series in chart.series:
        chart_data.add_series(series.name, series.values)

    slide.shapes.add_chart(
        _CHART_TYPE_MAP[chart.chart_type],
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
        chart_data,
    )
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


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
