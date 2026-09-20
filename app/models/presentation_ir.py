from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.template_manifest import LayoutType

MAX_BULLETS = 6
MAX_WORDS_PER_BULLET = 15
MAX_TABLE_COLS = 7
MAX_TABLE_ROWS = 5
BANNED_SUBSTRINGS = ("todo", "lorem ipsum", "xxx", "placeholder", "тбд", "заглушка")


class TitleComponent(BaseModel):
    type: Literal["title"] = "title"
    text: str = Field(min_length=1, max_length=120)
    is_action_title: bool = False


class BulletItem(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def check_bullet(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty bullet text")
        if len(v.split()) > MAX_WORDS_PER_BULLET:
            raise ValueError(f"bullet exceeds {MAX_WORDS_PER_BULLET} words")
        return v


class BulletBlock(BaseModel):
    type: Literal["bullet_block"] = "bullet_block"
    items: list[BulletItem] = Field(min_length=1, max_length=MAX_BULLETS)


class MetricCard(BaseModel):
    type: Literal["metric_card"] = "metric_card"
    label: str = Field(min_length=1, max_length=60)
    value: str = Field(min_length=1, max_length=20)
    delta: str | None = None
    trend: Literal["up", "down", "flat"] | None = None


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class ChartData(BaseModel):
    type: Literal["chart"] = "chart"
    chart_type: Literal["bar", "column", "line", "pie"] = "column"
    categories: list[str] = Field(min_length=1, max_length=12)
    series: list[ChartSeries] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def check_series_lengths(self) -> ChartData:
        for s in self.series:
            if len(s.values) != len(self.categories):
                raise ValueError(
                    f"series {s.name!r} length {len(s.values)} != "
                    f"categories length {len(self.categories)}"
                )
        return self


class TableData(BaseModel):
    type: Literal["table"] = "table"
    headers: list[str] = Field(min_length=1, max_length=MAX_TABLE_COLS)
    rows: list[list[str]] = Field(min_length=1, max_length=MAX_TABLE_ROWS)

    @model_validator(mode="after")
    def check_row_shape(self) -> TableData:
        for i, row in enumerate(self.rows):
            if len(row) != len(self.headers):
                raise ValueError(
                    f"row {i} has {len(row)} cells, expected {len(self.headers)}"
                )
        return self


class ImagePlaceholder(BaseModel):
    type: Literal["image"] = "image"
    alt_text: str
    role: Literal["hero", "icon", "logo", "illustration"] = "illustration"


SlideComponent = Annotated[
    BulletBlock | MetricCard | ChartData | TableData | ImagePlaceholder,
    Field(discriminator="type"),
]


class SlideIR(BaseModel):
    slide_index: int = Field(ge=0)
    layout_type: LayoutType
    title: TitleComponent
    components: list[SlideComponent] = Field(default_factory=list)
    speaker_notes: str = ""

    @field_validator("title")
    @classmethod
    def title_no_placeholder(cls, v: TitleComponent) -> TitleComponent:
        low = v.text.lower()
        if any(b in low for b in BANNED_SUBSTRINGS):
            raise ValueError(f"placeholder text detected in title: {v.text!r}")
        return v


class PresentationIR(BaseModel):
    variant: Literal["A", "B", "C"]
    template_source_hash: str
    slides: list[SlideIR] = Field(min_length=10, max_length=15)

    @model_validator(mode="after")
    def check_unique_indices(self) -> PresentationIR:
        indices = [s.slide_index for s in self.slides]
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate slide_index values")
        return self
