from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pptx import Presentation
from pptx.presentation import Presentation as PptxPresentation
from pptx.shapes.base import BaseShape
from pptx.slide import Slide, SlideLayout

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
_COLUMN_GAP_EMU = 137_160  # 0.15in gap between two bullet-block columns sharing one slot

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


def _group_components(components: list[SlideComponent], pair_bullet_blocks: bool) -> list[list[SlideComponent]]:
    groups: list[list[SlideComponent]] = []
    i = 0
    while i < len(components):
        component = components[i]
        if isinstance(component, MetricCard):
            j = i
            while j < len(components) and isinstance(components[j], MetricCard):
                j += 1
            groups.append(components[i:j])
            i = j
            continue
        if (
            pair_bullet_blocks
            and isinstance(component, BulletBlock)
            and i + 1 < len(components)
            and isinstance(components[i + 1], BulletBlock)
        ):
            groups.append([component, components[i + 1]])
            i += 2
            continue
        groups.append([component])
        i += 1
    return groups


class PptxBuilder:
    def build(self, template_path: str, manifest: TemplateManifest, ir: PresentationIR) -> BuildResult:
        prs = Presentation(template_path)
        self._strip_existing_slides(prs)
        all_layouts = self._all_slide_layouts(prs)

        bbox_map: dict[int, dict[str, BBox]] = {}
        for slide_ir in ir.slides:
            layout = manifest.find_layout_or_fallback(slide_ir.layout_type, slide_ir.slide_index)
            pptx_layout = all_layouts[layout.layout_index]
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

            bullet_block_count = sum(1 for c in slide_ir.components if isinstance(c, BulletBlock))
            available_body_slots = sum(
                1 for s in layout.slots
                if s.placeholder_type == PlaceholderType.BODY and s.placeholder_idx not in used_placeholder_idxs
            )
            pair_bullet_blocks = bullet_block_count == 2 and available_body_slots < 2

            component_index = 0
            for group in _group_components(slide_ir.components, pair_bullet_blocks):
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

                if len(group) == 2 and all(isinstance(c, BulletBlock) for c in group):
                    # the matched slot (or fallback box) becomes a shared container split into two
                    # columns, so the original single-region placeholder is left unclaimed here and
                    # gets purged below rather than showing its own ghost text under the columns
                    container_geometry, _, _ = self._resolve_geometry(
                        slide, group[0], layout, used_placeholder_idxs, prs, cursor_bottom_emu
                    )
                    for block, col_geometry in zip(group, self._split_into_columns(container_geometry)):
                        bbox = render_bullet_component(
                            slide, col_geometry, None, block, font_path, manifest.fonts.minor_latin
                        )
                        shape_boxes[f"component_{component_index}"] = bbox
                        component_index += 1
                    cursor_bottom_emu = container_geometry.top_emu + container_geometry.height_emu + SLIDE_MARGIN_EMU
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

    def _all_slide_layouts(self, prs: PptxPresentation) -> list[SlideLayout]:
        return [layout for master in prs.slide_masters for layout in master.slide_layouts]

    def _split_into_columns(self, geometry: Geometry) -> tuple[Geometry, Geometry]:
        col_width = (geometry.width_emu - _COLUMN_GAP_EMU) // 2
        left_col = Geometry(
            left_emu=geometry.left_emu, top_emu=geometry.top_emu,
            width_emu=col_width, height_emu=geometry.height_emu,
        )
        right_col = Geometry(
            left_emu=geometry.left_emu + col_width + _COLUMN_GAP_EMU, top_emu=geometry.top_emu,
            width_emu=col_width, height_emu=geometry.height_emu,
        )
        return left_col, right_col

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
