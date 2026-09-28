from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

from app.core.parser.layout_classifier import classify_layout
from app.core.parser.theme_extractor import extract_font_scheme, extract_theme_colors
from app.models.template_manifest import (
    FontScheme,
    Geometry,
    LayoutManifest,
    LayoutSlot,
    NormalizedGeometry,
    PlaceholderType,
    TemplateManifest,
    ThemeColors,
)

logger = logging.getLogger(__name__)

_THEME_PATH = "ppt/theme/theme1.xml"

_PLACEHOLDER_TYPE_MAP: dict[int, PlaceholderType] = {
    PP_PLACEHOLDER.TITLE: PlaceholderType.TITLE,
    PP_PLACEHOLDER.CENTER_TITLE: PlaceholderType.TITLE,
    PP_PLACEHOLDER.VERTICAL_TITLE: PlaceholderType.TITLE,
    PP_PLACEHOLDER.SUBTITLE: PlaceholderType.SUBTITLE,
    PP_PLACEHOLDER.BODY: PlaceholderType.BODY,
    PP_PLACEHOLDER.VERTICAL_BODY: PlaceholderType.BODY,
    PP_PLACEHOLDER.OBJECT: PlaceholderType.BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT: PlaceholderType.BODY,
    PP_PLACEHOLDER.PICTURE: PlaceholderType.PICTURE,
    PP_PLACEHOLDER.TABLE: PlaceholderType.TABLE,
    PP_PLACEHOLDER.CHART: PlaceholderType.CHART,
    PP_PLACEHOLDER.FOOTER: PlaceholderType.FOOTER,
    PP_PLACEHOLDER.SLIDE_NUMBER: PlaceholderType.SLIDE_NUMBER,
    PP_PLACEHOLDER.DATE: PlaceholderType.DATE,
}


class TemplateParseError(Exception):
    pass


def _resolved_placeholder_box(placeholder, master) -> tuple[int, int, int, int] | None:
    values = (placeholder.left, placeholder.top, placeholder.width, placeholder.height)
    if None not in values:
        return values
    idx = placeholder.placeholder_format.idx
    inherited = next(
        (
            candidate
            for candidate in master.placeholders
            if candidate.placeholder_format.idx == idx
        ),
        None,
    )
    if inherited is None:
        return None
    inherited_values = (
        inherited.left,
        inherited.top,
        inherited.width,
        inherited.height,
    )
    return inherited_values if None not in inherited_values else None


def _map_placeholder_type(ph_type: int | None) -> PlaceholderType:
    if ph_type is None:
        return PlaceholderType.OTHER
    return _PLACEHOLDER_TYPE_MAP.get(ph_type, PlaceholderType.OTHER)


def _read_theme_bytes(pptx_path: str) -> bytes:
    with zipfile.ZipFile(pptx_path) as zf:
        if _THEME_PATH not in zf.namelist():
            return b""
        return zf.read(_THEME_PATH)


class TemplateParser:
    def parse(self, pptx_path: str) -> TemplateManifest:
        file_bytes = Path(pptx_path).read_bytes()
        source_hash = hashlib.sha256(file_bytes).hexdigest()

        cache_dir = Path(".cache")
        cache_path = cache_dir / f"{source_hash}.manifest.json"
        cache_writable = True
        try:
            cache_dir.mkdir(exist_ok=True)
        except OSError:
            logger.warning("cache directory %s is not writable, caching disabled", cache_dir)
            cache_writable = False

        if cache_writable and cache_path.exists():
            try:
                return TemplateManifest.model_validate_json(cache_path.read_text())
            except OSError:
                logger.warning("cannot read template cache %s; reparsing", cache_path)

        try:
            prs = Presentation(pptx_path)
        except Exception as exc:
            raise TemplateParseError(f"cannot open pptx: {exc}") from exc

        theme_bytes = _read_theme_bytes(pptx_path)
        colors = ThemeColors(**extract_theme_colors(theme_bytes))
        major_latin, minor_latin = extract_font_scheme(theme_bytes)
        fonts = FontScheme(major_latin=major_latin, minor_latin=minor_latin)

        layouts: list[LayoutManifest] = []
        layout_index = 0
        # prs.slide_layouts only exposes the first slide master's layouts; a corporate
        # template's real content layouts often live on additional masters, so every
        # master must be walked or the manifest silently only sees a handful of title slides
        for master in prs.slide_masters:
            for layout in master.slide_layouts:
                slots: list[LayoutSlot] = []
                for placeholder in layout.placeholders:
                    resolved_box = _resolved_placeholder_box(placeholder, master)
                    if resolved_box is None:
                        continue
                    left, top, width, height = resolved_box
                    geometry = Geometry(
                        left_emu=left,
                        top_emu=top,
                        width_emu=width,
                        height_emu=height,
                    )
                    normalized = NormalizedGeometry(
                        x=left / prs.slide_width,
                        y=top / prs.slide_height,
                        w=width / prs.slide_width,
                        h=height / prs.slide_height,
                    )
                    slots.append(
                        LayoutSlot(
                            placeholder_idx=placeholder.placeholder_format.idx,
                            placeholder_type=_map_placeholder_type(placeholder.placeholder_format.type),
                            geometry=geometry,
                            normalized=normalized,
                            name=placeholder.name,
                        )
                    )
                layout_type = classify_layout(layout.name, slots)
                layouts.append(
                    LayoutManifest(
                        layout_index=layout_index,
                        layout_name=layout.name,
                        layout_type=layout_type,
                        slots=slots,
                    )
                )
                layout_index += 1

        manifest = TemplateManifest(
            source_hash=source_hash,
            slide_width_emu=prs.slide_width,
            slide_height_emu=prs.slide_height,
            colors=colors,
            fonts=fonts,
            layouts=layouts,
        )

        if cache_writable:
            try:
                cache_path.write_text(manifest.model_dump_json())
            except OSError:
                logger.warning("cannot write template cache %s; caching disabled", cache_path)
        return manifest
