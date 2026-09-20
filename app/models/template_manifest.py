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


class TemplateManifest(BaseModel):
    source_hash: str
    slide_width_emu: int
    slide_height_emu: int
    colors: ThemeColors
    fonts: FontScheme
    layouts: list[LayoutManifest]

    def find_layout(self, layout_type: LayoutType) -> LayoutManifest | None:
        return next((l for l in self.layouts if l.layout_type == layout_type), None)

    def find_layout_or_fallback(self, layout_type: LayoutType) -> LayoutManifest:
        found = self.find_layout(layout_type)
        if found is not None:
            return found
        for l in self.layouts:
            if l.slot_by_type(PlaceholderType.BODY) is not None:
                return l
        return self.layouts[0]
