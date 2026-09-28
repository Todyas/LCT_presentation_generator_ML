from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class PlaceholderType(str, Enum):
    TITLE = "TITLE"
    SUBTITLE = "SUBTITLE"
    BODY = "BODY"
    PICTURE = "PICTURE"
    TABLE = "TABLE"
    CHART = "CHART"
    FOOTER = "FOOTER"
    SLIDE_NUMBER = "SLIDE_NUMBER"
    DATE = "DATE"
    OTHER = "OTHER"


class LayoutType(str, Enum):
    TITLE_SLIDE = "TITLE_SLIDE"
    SECTION_HEADER = "SECTION_HEADER"
    CONTENT_1COL = "CONTENT_1COL"
    CONTENT_2COL = "CONTENT_2COL"
    COMPARISON = "COMPARISON"
    TABLE_FOCUSED = "TABLE_FOCUSED"
    CHART_FOCUSED = "CHART_FOCUSED"
    KPI_DASHBOARD = "KPI_DASHBOARD"
    PROCESS_TIMELINE = "PROCESS_TIMELINE"
    QUOTE = "QUOTE"
    BLANK = "BLANK"
    UNKNOWN = "UNKNOWN"


class Geometry(BaseModel):
    left_emu: int
    top_emu: int
    width_emu: int = Field(gt=0)
    height_emu: int = Field(gt=0)

    @property
    def right_emu(self) -> int:
        return self.left_emu + self.width_emu

    @property
    def bottom_emu(self) -> int:
        return self.top_emu + self.height_emu


class NormalizedGeometry(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class LayoutSlot(BaseModel):
    placeholder_idx: int
    placeholder_type: PlaceholderType
    geometry: Geometry
    normalized: NormalizedGeometry
    name: str = ""
    inferred: bool = False


class LayoutManifest(BaseModel):
    layout_index: int
    layout_name: str
    layout_type: LayoutType
    slots: list[LayoutSlot]

    def slot_by_type(self, t: PlaceholderType) -> LayoutSlot | None:
        return next((s for s in self.slots if s.placeholder_type == t), None)


class ThemeColors(BaseModel):
    dk1: str
    lt1: str
    dk2: str
    lt2: str
    accent1: str
    accent2: str
    accent3: str
    accent4: str
    accent5: str
    accent6: str
    hlink: str
    fol_hlink: str

    @field_validator("*")
    @classmethod
    def must_be_hex6(cls, v: str) -> str:
        v = v.upper().lstrip("#")
        if len(v) != 6 or any(c not in "0123456789ABCDEF" for c in v):
            raise ValueError(f"invalid hex color: {v!r}")
        return v


class FontScheme(BaseModel):
    major_latin: str = "Calibri"
    minor_latin: str = "Calibri"


class BrandProfile(BaseModel):
    title_size_pt: float = Field(default=32, ge=16, le=54)
    body_size_pt: float = Field(default=20, ge=10, le=32)
    sampled_colors: list[str] = Field(default_factory=list, max_length=12)


class TemplateManifest(BaseModel):
    source_hash: str
    slide_width_emu: int
    slide_height_emu: int
    colors: ThemeColors
    fonts: FontScheme
    brand_profile: BrandProfile = Field(default_factory=BrandProfile)
    layouts: list[LayoutManifest]

    def find_layout(self, layout_type: LayoutType) -> LayoutManifest | None:
        return next((l for l in self.layouts if l.layout_type == layout_type), None)

    def find_layout_or_fallback(self, layout_type: LayoutType, slide_index: int = 0) -> LayoutManifest:
        if slide_index == 0:
            found = self.find_layout(layout_type)
            if found is not None:
                return found
            for l in self.layouts:
                if l.slot_by_type(PlaceholderType.BODY) is not None:
                    return l
            return self.layouts[0]

        # a TITLE_SLIDE layout is usually a branded cover with a large decorative
        # graphic (see layout_classifier.py) that only makes sense on slide 0 — every
        # other slide must land on a content layout even if one was explicitly requested
        found = self.find_layout(layout_type)
        if found is not None and found.layout_type != LayoutType.TITLE_SLIDE:
            return found
        # Semantic layouts such as KPI, chart, table, and timeline are often absent
        # from corporate templates.  In that case a regular content canvas is much
        # safer than a section divider whose title may live in the middle or bottom.
        for preferred_type in (LayoutType.CONTENT_1COL, LayoutType.CONTENT_2COL):
            preferred = self.find_layout(preferred_type)
            if preferred is not None:
                return preferred
        for l in self.layouts:
            if l.layout_type != LayoutType.TITLE_SLIDE and l.slot_by_type(PlaceholderType.BODY) is not None:
                return l
        non_title_layouts = [
            layout for layout in self.layouts
            if layout.layout_type != LayoutType.TITLE_SLIDE
        ]
        if non_title_layouts:
            chrome = {
                PlaceholderType.TITLE,
                PlaceholderType.FOOTER,
                PlaceholderType.DATE,
                PlaceholderType.SLIDE_NUMBER,
            }

            def usable_area(layout: LayoutManifest) -> float:
                return sum(
                    slot.normalized.w * slot.normalized.h
                    for slot in layout.slots
                    if slot.placeholder_type not in chrome
                )

            return max(non_title_layouts, key=usable_area)
        for l in self.layouts:
            if l.slot_by_type(PlaceholderType.BODY) is not None:
                return l
        return self.layouts[0]
