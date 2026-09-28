from __future__ import annotations

import re
from dataclasses import dataclass

from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_FILL_TYPE, MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.shapes.base import BaseShape
from pptx.shapes.placeholder import ChartPlaceholder, TablePlaceholder
from pptx.slide import Slide
from pptx.table import Table
from pptx.text.text import TextFrame
from pptx.util import Emu, Pt

from app.core.builder.autofit import apply_autofit_to_text_frame, autofit_font_size
from app.models.presentation_ir import (
    BulletBlock,
    ChartData,
    ComparisonData,
    IconListData,
    MetricCard,
    ProcessData,
    TableData,
)
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


def _is_dark(hex_color: str) -> bool:
    red, green, blue = (int(hex_color[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue < 118


def _surface_palette(slide: Slide, theme: ThemeColors, width: int, height: int) -> tuple[str, str, str]:
    background = _background_hex(slide, theme, width, height)
    if _is_dark(background):
        return theme.dk2, theme.lt1, theme.accent5
    return theme.lt1, theme.dk1, theme.accent1


def _style_text_frame(text_frame: TextFrame, text: str, color: str, font_name: str, size: int, *, bold: bool = False) -> None:
    text_frame.clear()
    text_frame.text = text
    text_frame.word_wrap = True
    for paragraph in text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.name = font_name
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = _rgb(color)


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


_THEME_COLOR_MAP = {
    MSO_THEME_COLOR.DARK_1: "dk1",
    MSO_THEME_COLOR.DARK_2: "dk2",
    MSO_THEME_COLOR.LIGHT_1: "lt1",
    MSO_THEME_COLOR.LIGHT_2: "lt2",
    MSO_THEME_COLOR.TEXT_1: "dk1",
    MSO_THEME_COLOR.TEXT_2: "dk2",
    MSO_THEME_COLOR.BACKGROUND_1: "lt1",
    MSO_THEME_COLOR.BACKGROUND_2: "lt2",
    MSO_THEME_COLOR.ACCENT_1: "accent1",
    MSO_THEME_COLOR.ACCENT_2: "accent2",
    MSO_THEME_COLOR.ACCENT_3: "accent3",
    MSO_THEME_COLOR.ACCENT_4: "accent4",
    MSO_THEME_COLOR.ACCENT_5: "accent5",
    MSO_THEME_COLOR.ACCENT_6: "accent6",
}


def _color_hex(color, theme: ThemeColors) -> str | None:
    try:
        if color.rgb is not None:
            return str(color.rgb)
    except (AttributeError, ValueError):
        pass
    try:
        theme_key = _THEME_COLOR_MAP.get(color.theme_color)
    except (AttributeError, ValueError):
        theme_key = None
    return getattr(theme, theme_key) if theme_key is not None else None


def _fill_hex(fill, theme: ThemeColors) -> str | None:
    if fill.type == MSO_FILL_TYPE.SOLID:
        return _color_hex(fill.fore_color, theme)
    if fill.type == MSO_FILL_TYPE.GRADIENT:
        colors = [
            value
            for stop in fill.gradient_stops
            if (value := _color_hex(stop.color, theme)) is not None
        ]
        if colors:
            return colors[0]
    return None


def _background_hex(
    slide: Slide,
    theme: ThemeColors,
    slide_width_emu: int,
    slide_height_emu: int,
) -> str:
    for owner in (slide, slide.slide_layout, slide.slide_layout.slide_master):
        color = _fill_hex(owner.background.fill, theme)
        if color is not None:
            return color

        full_area = slide_width_emu * slide_height_emu
        covering_shapes = sorted(
            (
                shape
                for shape in owner.shapes
                if shape.width * shape.height >= full_area * 0.55
                and shape.left <= slide_width_emu * 0.1
                and shape.top <= slide_height_emu * 0.1
                and hasattr(shape, "fill")
            ),
            key=lambda shape: shape.width * shape.height,
            reverse=True,
        )
        for shape in covering_shapes:
            color = _fill_hex(shape.fill, theme)
            if color is not None:
                return color
    return theme.lt1


def _contrasting_text_hex(background_hex: str, theme: ThemeColors) -> str:
    red, green, blue = (
        int(background_hex[index : index + 2], 16) / 255
        for index in (0, 2, 4)
    )
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return theme.lt1 if luminance < 0.48 else theme.dk1


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


def render_title_component(
    slide: Slide,
    geometry: Geometry,
    text: str,
    placeholder_shape: BaseShape | None,
    font_path: str,
    font_name: str,
    theme: ThemeColors,
    slide_width_emu: int,
    slide_height_emu: int,
    max_size_pt: float = 32,
) -> BBox:
    generated = placeholder_shape is None
    if placeholder_shape is None:
        shape = slide.shapes.add_textbox(
            Emu(geometry.left_emu), Emu(geometry.top_emu),
            Emu(geometry.width_emu), Emu(geometry.height_emu),
        )
    else:
        shape = placeholder_shape
    shape.text_frame.text = text
    shape.text_frame.word_wrap = True
    apply_autofit_to_text_frame(
        shape.text_frame,
        geometry,
        font_path,
        max_size_pt=max_size_pt,
        line_spacing=1.15,
    )
    fallback_color = (
        _contrasting_text_hex(
            _background_hex(slide, theme, slide_width_emu, slide_height_emu), theme
        )
        if generated
        else None
    )
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.name = font_name
            run.font.bold = True
            if fallback_color is not None:
                run.font.color.rgb = _rgb(fallback_color)
    return BBox(shape.left, shape.top, shape.width, shape.height)


def render_bullet_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    block: BulletBlock,
    font_path: str,
    font_name: str,
    theme: ThemeColors,
    slide_width_emu: int,
    slide_height_emu: int,
    max_size_pt: float = 24,
) -> BBox:
    generated_textbox = placeholder_shape is None
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
    if generated_textbox:
        text_frame.margin_left = Pt(6)
        text_frame.margin_right = Pt(6)
        text_frame.margin_top = Pt(6)
        text_frame.margin_bottom = Pt(6)
    text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    apply_autofit_to_text_frame(
        text_frame, geometry, font_path, max_size_pt=max_size_pt, line_spacing=1.2
    )
    fallback_color = (
        _contrasting_text_hex(
            _background_hex(slide, theme, slide_width_emu, slide_height_emu),
            theme,
        )
        if generated_textbox
        else None
    )
    for index, paragraph in enumerate(text_frame.paragraphs):
        paragraph.space_after = Pt(14)
        paragraph.line_spacing = 1.12
        if generated_textbox:
            paragraph.alignment = PP_ALIGN.LEFT
        paragraph_has_bold = any(run.font.bold for run in paragraph.runs)
        for run in paragraph.runs:
            if fallback_color is not None:
                run.font.color.rgb = _rgb(fallback_color)
            if index == 0 and not paragraph_has_bold:
                run.font.bold = True
    return bbox


def render_metric_card(
    slide: Slide,
    geometry: Geometry,
    card: MetricCard,
    theme: ThemeColors,
    font_path: str,
    font_name: str | None = None,
) -> BBox:
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
    value_box.text_frame.paragraphs[0].runs[0].font.color.rgb = _rgb(theme.accent1)
    if font_name:
        value_box.text_frame.paragraphs[0].runs[0].font.name = font_name

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
    label_box.text_frame.paragraphs[0].runs[0].font.color.rgb = _rgb(theme.dk1)
    if font_name:
        label_box.text_frame.paragraphs[0].runs[0].font.name = font_name

    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_metric_card_group(
    slide: Slide,
    geometry: Geometry,
    cards: list[MetricCard],
    theme: ThemeColors,
    font_path: str,
    font_name: str | None = None,
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
        boxes.append(
            render_metric_card(slide, card_geometry, card, theme, font_path, font_name)
        )
    return boxes


def fill_table_cells(
    pptx_table: Table,
    table: TableData,
    theme: ThemeColors | None = None,
    font_name: str | None = None,
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
                if font_name:
                    run.font.name = font_name
    for r in range(1, len(table.rows) + 1):
        for c in range(len(table.headers)):
            cell = pptx_table.cell(r, c)
            if r % 2 == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = _rgb(theme.lt2)
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = _rgb(theme.dk1)
                    if font_name:
                        run.font.name = font_name


def render_table(
    slide: Slide,
    geometry: Geometry,
    table: TableData,
    theme: ThemeColors | None = None,
    font_name: str | None = None,
) -> BBox:
    rows = len(table.rows) + 1
    cols = len(table.headers)
    graphic_frame = slide.shapes.add_table(
        rows, cols,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    fill_table_cells(graphic_frame.table, table, theme, font_name)
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_table_into_placeholder(
    placeholder: TablePlaceholder,
    table: TableData,
    theme: ThemeColors | None = None,
    font_name: str | None = None,
) -> BBox:
    graphic_frame = placeholder.insert_table(len(table.rows) + 1, len(table.headers))
    fill_table_cells(graphic_frame.table, table, theme, font_name)
    return BBox(graphic_frame.left, graphic_frame.top, graphic_frame.width, graphic_frame.height)


def render_table_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    table: TableData,
    theme: ThemeColors,
    font_name: str | None = None,
) -> BBox:
    if isinstance(placeholder_shape, TablePlaceholder):
        return render_table_into_placeholder(placeholder_shape, table, theme, font_name)
    return render_table(slide, geometry, table, theme, font_name)


def build_chart_data(chart: ChartData) -> CategoryChartData:
    chart_data = CategoryChartData()
    chart_data.categories = chart.categories
    for series in chart.series:
        chart_data.add_series(series.name, series.values)
    return chart_data


def _style_chart(chart_shape, theme: ThemeColors | None) -> None:
    chart_shape.chart_style = 10
    chart_shape.has_legend = len(chart_shape.series) > 1
    if theme is None:
        return
    try:
        chart_shape.chart_area.format.fill.background()
        chart_shape.plot_area.format.fill.background()
    except (AttributeError, ValueError):
        pass
    palette = [theme.accent1, theme.accent2, theme.accent5, theme.accent6]
    for index, series in enumerate(chart_shape.series):
        color = _rgb(palette[index % len(palette)])
        try:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = color
        except (AttributeError, ValueError):
            pass
        try:
            series.format.line.color.rgb = color
        except (AttributeError, ValueError):
            pass


def render_chart(
    slide: Slide, geometry: Geometry, chart: ChartData, theme: ThemeColors | None = None
) -> BBox:
    chart_data = build_chart_data(chart)
    graphic_frame = slide.shapes.add_chart(
        CHART_TYPE_MAP[chart.chart_type],
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
        chart_data,
    )
    _style_chart(graphic_frame.chart, theme)
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_chart_into_placeholder(
    placeholder: ChartPlaceholder, chart: ChartData, theme: ThemeColors | None = None
) -> BBox:
    graphic_frame = placeholder.insert_chart(CHART_TYPE_MAP[chart.chart_type], build_chart_data(chart))
    _style_chart(graphic_frame.chart, theme)
    return BBox(graphic_frame.left, graphic_frame.top, graphic_frame.width, graphic_frame.height)


def render_chart_component(
    slide: Slide,
    geometry: Geometry,
    placeholder_shape: BaseShape | None,
    chart: ChartData,
    theme: ThemeColors | None = None,
) -> BBox:
    if isinstance(placeholder_shape, ChartPlaceholder):
        return render_chart_into_placeholder(placeholder_shape, chart, theme)
    return render_chart(slide, geometry, chart, theme)


def render_comparison(
    slide: Slide,
    geometry: Geometry,
    comparison: ComparisonData,
    theme: ThemeColors,
    font_name: str,
    slide_width_emu: int,
    slide_height_emu: int,
) -> BBox:
    surface, text_color, accent = _surface_palette(slide, theme, slide_width_emu, slide_height_emu)
    gap = 180_000
    width = (geometry.width_emu - gap) // 2
    sides = (
        (comparison.left_title, comparison.left_items, theme.accent2),
        (comparison.right_title, comparison.right_items, accent),
    )
    body_size = 14 if len(comparison.left_items) + len(comparison.right_items) <= 6 else 12
    for index, (heading, items, side_color) in enumerate(sides):
        left = geometry.left_emu + index * (width + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Emu(left), Emu(geometry.top_emu), Emu(width), Emu(geometry.height_emu),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = _rgb(surface)
        card.line.color.rgb = _rgb(side_color)
        band_height = min(650_000, geometry.height_emu // 4)
        band = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Emu(left), Emu(geometry.top_emu), Emu(width), Emu(band_height),
        )
        band.fill.solid()
        band.fill.fore_color.rgb = _rgb(side_color)
        band.line.fill.background()
        _style_text_frame(band.text_frame, heading, theme.lt1, font_name, 18, bold=True)
        band.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        band.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        body = slide.shapes.add_textbox(
            Emu(left + 90_000), Emu(geometry.top_emu + band_height + 70_000),
            Emu(width - 180_000), Emu(geometry.height_emu - band_height - 140_000),
        )
        block = BulletBlock(items=[{"text": value} for value in items])
        render_bullet_block(body.text_frame, block, "", font_name)
        for paragraph in body.text_frame.paragraphs:
            paragraph.space_after = Pt(10)
            for run in paragraph.runs:
                run.font.color.rgb = _rgb(text_color)
                run.font.size = Pt(body_size)
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_process(
    slide: Slide,
    geometry: Geometry,
    process: ProcessData,
    theme: ThemeColors,
    font_name: str,
    slide_width_emu: int,
    slide_height_emu: int,
) -> BBox:
    _, text_color, accent = _surface_palette(slide, theme, slide_width_emu, slide_height_emu)
    count = len(process.steps)
    gap = 95_000
    step_width = (geometry.width_emu - gap * (count - 1)) // count
    circle_size = min(520_000, step_width // 2)
    line_top = geometry.top_emu + circle_size // 2
    if count > 1:
        connector = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Emu(geometry.left_emu + circle_size // 2), Emu(line_top - 18_000),
            Emu(geometry.width_emu - circle_size), Emu(36_000),
        )
        connector.fill.solid()
        connector.fill.fore_color.rgb = _rgb(accent)
        connector.line.fill.background()
    for index, step in enumerate(process.steps):
        left = geometry.left_emu + index * (step_width + gap)
        circle = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Emu(left + (step_width - circle_size) // 2),
            Emu(geometry.top_emu), Emu(circle_size), Emu(circle_size),
        )
        circle.fill.solid()
        circle.fill.fore_color.rgb = _rgb(accent)
        circle.line.fill.background()
        _style_text_frame(circle.text_frame, str(index + 1), theme.lt1, font_name, 18, bold=True)
        circle.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        circle.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        text_box = slide.shapes.add_textbox(
            Emu(left), Emu(geometry.top_emu + circle_size + 75_000),
            Emu(step_width), Emu(geometry.height_emu - circle_size - 75_000),
        )
        content = step.title if not step.description else f"{step.title}\n{step.description}"
        text_size = 14 if count <= 4 else 11
        _style_text_frame(
            text_box.text_frame, content, text_color, font_name, text_size, bold=False
        )
        text_box.text_frame.paragraphs[0].runs[0].font.bold = True
        text_box.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_icon_list(
    slide: Slide,
    geometry: Geometry,
    icon_list: IconListData,
    theme: ThemeColors,
    font_name: str,
    slide_width_emu: int,
    slide_height_emu: int,
) -> BBox:
    surface, text_color, accent = _surface_palette(slide, theme, slide_width_emu, slide_height_emu)
    columns = min(len(icon_list.items), 2)
    rows = (len(icon_list.items) + columns - 1) // columns
    gap = 120_000
    cell_width = (geometry.width_emu - gap * (columns - 1)) // columns
    cell_height = (geometry.height_emu - gap * (rows - 1)) // rows
    icon_size = min(440_000, cell_height - 100_000)
    symbols = {"check": "✓", "shield": "◆", "speed": "➜", "people": "●", "cloud": "☁", "gear": "⚙", "chart": "↗", "star": "★"}
    for index, item in enumerate(icon_list.items):
        row, column = divmod(index, columns)
        left = geometry.left_emu + column * (cell_width + gap)
        top = geometry.top_emu + row * (cell_height + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Emu(left), Emu(top), Emu(cell_width), Emu(cell_height),
        )
        card.fill.solid()
        card.fill.fore_color.rgb = _rgb(surface)
        card.line.color.rgb = _rgb(theme.lt2 if not _is_dark(surface) else theme.dk2)
        icon = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Emu(left + 80_000), Emu(top + (cell_height - icon_size) // 2),
            Emu(icon_size), Emu(icon_size),
        )
        icon.fill.solid()
        icon.fill.fore_color.rgb = _rgb(accent)
        icon.line.fill.background()
        _style_text_frame(icon.text_frame, symbols[item.icon], theme.lt1, font_name, 15, bold=True)
        icon.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        icon.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
        text_box = slide.shapes.add_textbox(
            Emu(left + icon_size + 150_000), Emu(top + 60_000),
            Emu(cell_width - icon_size - 220_000), Emu(cell_height - 120_000),
        )
        content = item.title if not item.description else f"{item.title}\n{item.description}"
        text_size = 14 if len(icon_list.items) <= 4 else 12
        _style_text_frame(text_box.text_frame, content, text_color, font_name, text_size)
        text_box.text_frame.paragraphs[0].runs[0].font.bold = True
        text_box.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)


def render_image_placeholder(
    slide: Slide, geometry: Geometry, alt_text: str, theme: ThemeColors
) -> BBox:
    """Render a brand-colored vector illustration when no external asset exists."""
    panel = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Emu(geometry.left_emu), Emu(geometry.top_emu),
        Emu(geometry.width_emu), Emu(geometry.height_emu),
    )
    panel.fill.solid()
    panel.fill.fore_color.rgb = _rgb(theme.dk2)
    panel.line.color.rgb = _rgb(theme.accent5)
    size = min(geometry.width_emu, geometry.height_emu) // 3
    centers = ((0.22, 0.3, theme.accent1), (0.58, 0.2, theme.accent5), (0.5, 0.58, theme.accent2))
    for x_ratio, y_ratio, color in centers:
        node = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            Emu(geometry.left_emu + int(geometry.width_emu * x_ratio)),
            Emu(geometry.top_emu + int(geometry.height_emu * y_ratio)),
            Emu(size), Emu(size),
        )
        node.fill.solid()
        node.fill.fore_color.rgb = _rgb(color)
        node.line.fill.background()
    # Keep the semantic description in the file without showing placeholder copy.
    try:
        panel.element.nvSpPr.cNvPr.set("descr", alt_text)
    except AttributeError:
        pass
    return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)
