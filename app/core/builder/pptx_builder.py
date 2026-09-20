from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path

from pptx import Presentation
from pptx.presentation import Presentation as PptxPresentation
from pptx.shapes.base import BaseShape
from pptx.slide import Slide

from app.core.builder.autofit import apply_autofit_to_text_frame
from app.core.builder.shape_factory import (
    BBox,
    render_bullet_component,
    render_chart_component,
    render_image_placeholder,
    render_metric_card_group,
    render_table_component,
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
_FALLBACK_WIDTH_RATIO = 0.8  # fraction of slide width used only when a layout has no content slots at all

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

# footer/date/slide-number chrome usually spans the full canvas width regardless of the
# layout's real content-safe region, so it must not seed the fallback bounding box (Bug 3)
_CHROME_PLACEHOLDER_TYPES = frozenset(
    {PlaceholderType.TITLE, PlaceholderType.FOOTER, PlaceholderType.DATE, PlaceholderType.SLIDE_NUMBER}
)


def _group_components(components: list[SlideComponent]) -> list[list[SlideComponent]]:
    groups: list[list[SlideComponent]] = []
    for is_metric, group in groupby(components, key=lambda c: isinstance(c, MetricCard)):
        chunk = list(group)
        if is_metric:
            groups.append(chunk)
        else:
            groups.extend([c] for c in chunk)
    return groups


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
            used_placeholder_idxs: set[int] = set()
            if title_placeholder is not None:
                title_placeholder.text_frame.text = slide_ir.title.text
                title_slot = layout.slot_by_type(PlaceholderType.TITLE)
                if title_slot is not None:
                    apply_autofit_to_text_frame(title_placeholder.text_frame, title_slot.geometry, font_path)
                shape_boxes["title"] = BBox(
                    title_placeholder.left, title_placeholder.top,
                    title_placeholder.width, title_placeholder.height,
                )
                used_placeholder_idxs.add(title_placeholder.placeholder_format.idx)

            cursor_bottom_emu = (
                title_placeholder.top + title_placeholder.height
                if title_placeholder is not None
                else 0
            )

            component_index = 0
            for group in _group_components(slide_ir.components):
                if isinstance(group[0], MetricCard):
                    geometry, _, _ = self._resolve_geometry(
                        slide, group[0], layout, used_placeholder_idxs, prs, cursor_bottom_emu
                    )
                    boxes = render_metric_card_group(slide, geometry, group, manifest.colors, font_path)
                    for bbox in boxes:
                        shape_boxes[f"component_{component_index}"] = bbox
                        component_index += 1
                    cursor_bottom_emu = geometry.top_emu + geometry.height_emu + SLIDE_MARGIN_EMU
                    continue

                component = group[0]
                geometry, slot_used, placeholder_shape = self._resolve_geometry(
                    slide, component, layout, used_placeholder_idxs, prs, cursor_bottom_emu
                )
                if slot_used is not None:
                    used_placeholder_idxs.add(slot_used)

                bbox = self._render_component(slide, component, geometry, placeholder_shape, manifest, font_path)
                shape_boxes[f"component_{component_index}"] = bbox
                component_index += 1
                cursor_bottom_emu = bbox.y + bbox.h + SLIDE_MARGIN_EMU

            self._purge_unused_placeholders(slide, used_placeholder_idxs)
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

    def _find_placeholder_by_idx(self, slide: Slide, idx: int) -> BaseShape | None:
        for shape in slide.placeholders:
            if shape.placeholder_format.idx == idx:
                return shape
        return None

    def _remove_shape(self, shape: BaseShape) -> None:
        shape.element.getparent().remove(shape.element)

    def _purge_unused_placeholders(self, slide: Slide, used_placeholder_idxs: set[int]) -> None:
        for shape in list(slide.placeholders):
            if shape.placeholder_format.idx not in used_placeholder_idxs:
                self._remove_shape(shape)

    def _resolve_geometry(
        self,
        slide: Slide,
        component: SlideComponent,
        layout: LayoutManifest,
        used_placeholder_idxs: set[int],
        prs: PptxPresentation,
        cursor_bottom_emu: int,
    ) -> tuple[Geometry, int | None, BaseShape | None]:
        wanted_type = _COMPONENT_TO_PLACEHOLDER_TYPE.get(type(component))
        if wanted_type is not None:
            for slot in layout.slots:
                if slot.placeholder_type == wanted_type and slot.placeholder_idx not in used_placeholder_idxs:
                    placeholder_shape = self._find_placeholder_by_idx(slide, slot.placeholder_idx)
                    return slot.geometry, slot.placeholder_idx, placeholder_shape

        return self._compute_fallback_geometry(layout, prs, cursor_bottom_emu), None, None

    def _compute_fallback_geometry(
        self, layout: LayoutManifest, prs: PptxPresentation, cursor_bottom_emu: int
    ) -> Geometry:
        content_slots = [s for s in layout.slots if s.placeholder_type not in _CHROME_PLACEHOLDER_TYPES]
        if content_slots:
            left = min(s.geometry.left_emu for s in content_slots)
            right = max(s.geometry.right_emu for s in content_slots)
            width = right - left
        else:
            width = int(prs.slide_width * _FALLBACK_WIDTH_RATIO)
            left = int((prs.slide_width - width) / 2)

        return Geometry(
            left_emu=left,
            top_emu=cursor_bottom_emu,
            width_emu=width,
            height_emu=_FALLBACK_HEIGHT_EMU,
        )

    def _render_component(
        self,
        slide: Slide,
        component: SlideComponent,
        geometry: Geometry,
        placeholder_shape: BaseShape | None,
        manifest: TemplateManifest,
        font_path: str,
    ) -> BBox:
        if isinstance(component, BulletBlock):
            return render_bullet_component(
                slide, geometry, placeholder_shape, component, font_path, manifest.fonts.minor_latin
            )
        if isinstance(component, TableData):
            return render_table_component(slide, geometry, placeholder_shape, component)
        if isinstance(component, ChartData):
            return render_chart_component(slide, geometry, placeholder_shape, component)
        if isinstance(component, ImagePlaceholder):
            bbox = render_image_placeholder(slide, geometry, component.alt_text, manifest.colors)
            if placeholder_shape is not None:
                self._remove_shape(placeholder_shape)
            return bbox
        raise TypeError(f"unhandled component type: {type(component)!r}")
