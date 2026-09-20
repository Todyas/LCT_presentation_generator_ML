from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.presentation import Presentation as PptxPresentation
from pptx.shapes.base import BaseShape
from pptx.slide import Slide

from app.core.builder.autofit import apply_autofit_to_text_frame
from app.core.builder.shape_factory import (
    BBox,
    render_bullet_block,
    render_chart,
    render_image_placeholder,
    render_metric_card,
    render_table,
)
from app.core.parser.font_resolver import resolve_font_path
from app.core.parser.template_parser import _PLACEHOLDER_TYPE_MAP
from app.models.presentation_ir import (
    BulletBlock,
    ChartData,
    ImagePlaceholder,
    MetricCard,
    PresentationIR,
    SlideComponent,
    TableData,
)
from app.models.template_manifest import (
    Geometry,
    LayoutManifest,
    PlaceholderType,
    TemplateManifest,
)


@dataclass
class BuildResult:
    pptx_path: str
    bbox_map: dict[int, dict[str, BBox]] = field(default_factory=dict)


SLIDE_MARGIN_EMU = 91_440  # 0.1 inch — spacing between an auto-placed component and its predecessor
_FALLBACK_HEIGHT_EMU = 1_500_000  # default box height when no matching slot exists, not a slide coordinate
_FALLBACK_WIDTH_RATIO = 0.8  # fraction of slide width used for the fallback box

# invert Sprint 1's PP_PLACEHOLDER -> PlaceholderType map so lookups stay consistent with the parser
_OUR_TO_PPTX_PLACEHOLDER_TYPES: dict[PlaceholderType, set[int]] = {}
for _pptx_type, _our_type in _PLACEHOLDER_TYPE_MAP.items():
    _OUR_TO_PPTX_PLACEHOLDER_TYPES.setdefault(_our_type, set()).add(int(_pptx_type))

_COMPONENT_TO_PLACEHOLDER_TYPE: dict[type, PlaceholderType] = {
    BulletBlock: PlaceholderType.BODY,
    TableData: PlaceholderType.TABLE,
    ChartData: PlaceholderType.CHART,
    ImagePlaceholder: PlaceholderType.PICTURE,
}


class PptxBuilder:
    def build(self, template_path: str, manifest: TemplateManifest, ir: PresentationIR) -> BuildResult:
        prs = Presentation(template_path)
        self._strip_existing_slides(prs)

        bbox_map: dict[int, dict[str, BBox]] = {}
        for slide_ir in ir.slides:
            layout = manifest.find_layout_or_fallback(slide_ir.layout_type)
            pptx_layout = prs.slide_layouts[layout.layout_index]
            slide = prs.slides.add_slide(pptx_layout)

            shape_boxes: dict[str, BBox] = {}
            font_path = resolve_font_path(manifest.fonts.minor_latin)

            title_placeholder = self._find_placeholder(slide, PlaceholderType.TITLE)
            if title_placeholder is not None:
                title_placeholder.text_frame.text = slide_ir.title.text
                title_slot = layout.slot_by_type(PlaceholderType.TITLE)
                if title_slot is not None:
                    apply_autofit_to_text_frame(title_placeholder.text_frame, title_slot.geometry, font_path)
                shape_boxes["title"] = BBox(
                    title_placeholder.left, title_placeholder.top,
                    title_placeholder.width, title_placeholder.height,
                )

            cursor_bottom_emu = (
                title_placeholder.top + title_placeholder.height
                if title_placeholder is not None
                else 0
            )

            used_slot_ids: set[int] = set()
            for i, component in enumerate(slide_ir.components):
                geometry, slot_used = self._resolve_geometry(
                    component, layout, used_slot_ids, prs, cursor_bottom_emu
                )
                if slot_used is not None:
                    used_slot_ids.add(slot_used)

                bbox = self._render_component(slide, component, geometry, manifest, font_path)
                shape_boxes[f"component_{i}"] = bbox
                cursor_bottom_emu = bbox.y + bbox.h + SLIDE_MARGIN_EMU

            bbox_map[slide_ir.slide_index] = shape_boxes

        output_path = str(Path(template_path).with_name(f"built_{ir.variant}.pptx"))
        prs.save(output_path)
        return BuildResult(pptx_path=output_path, bbox_map=bbox_map)

    def _strip_existing_slides(self, prs: PptxPresentation) -> None:
        xml_slides = prs.slides._sldIdLst
        slide_ids = list(xml_slides)
        for slide_id in slide_ids:
            rId = slide_id.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            prs.part.drop_rel(rId)
            xml_slides.remove(slide_id)

    def _find_placeholder(self, slide: Slide, ptype: PlaceholderType) -> BaseShape | None:
        target = _OUR_TO_PPTX_PLACEHOLDER_TYPES.get(ptype, set())
        for shape in slide.placeholders:
            if shape.placeholder_format.type in target:
                return shape
        return None

    def _resolve_geometry(
        self,
        component: SlideComponent,
        layout: LayoutManifest,
        used_slot_ids: set[int],
        prs: PptxPresentation,
        cursor_bottom_emu: int,
    ) -> tuple[Geometry, int | None]:
        wanted_type = _COMPONENT_TO_PLACEHOLDER_TYPE.get(type(component))
        if wanted_type is not None:
            for slot in layout.slots:
                if slot.placeholder_type == wanted_type and slot.placeholder_idx not in used_slot_ids:
                    return slot.geometry, slot.placeholder_idx

        fallback_width = int(prs.slide_width * _FALLBACK_WIDTH_RATIO)
        fallback_left = int((prs.slide_width - fallback_width) / 2)
        return (
            Geometry(
                left_emu=fallback_left,
                top_emu=cursor_bottom_emu,
                width_emu=fallback_width,
                height_emu=_FALLBACK_HEIGHT_EMU,
            ),
            None,
        )

    def _render_component(
        self,
        slide: Slide,
        component: SlideComponent,
        geometry: Geometry,
        manifest: TemplateManifest,
        font_path: str,
    ) -> BBox:
        if isinstance(component, BulletBlock):
            textbox = slide.shapes.add_textbox(
                geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu
            )
            render_bullet_block(textbox.text_frame, component, font_path, manifest.fonts.minor_latin)
            apply_autofit_to_text_frame(textbox.text_frame, geometry, font_path)
            return BBox(geometry.left_emu, geometry.top_emu, geometry.width_emu, geometry.height_emu)
        if isinstance(component, MetricCard):
            return render_metric_card(slide, geometry, component, manifest.colors)
        if isinstance(component, TableData):
            return render_table(slide, geometry, component)
        if isinstance(component, ChartData):
            return render_chart(slide, geometry, component)
        if isinstance(component, ImagePlaceholder):
            return render_image_placeholder(slide, geometry, component.alt_text, manifest.colors)
        raise TypeError(f"unhandled component type: {type(component)!r}")
