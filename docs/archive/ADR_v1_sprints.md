# ADR: «Цифровой дизайнер презентаций» — архитектура и ТЗ для реализации

Статус: принято. Срок реализации: 7 дней. Стек: Python 3.13, FastAPI, python-pptx, Pydantic v2, Qwen 2.5/3 (OpenAI-совместимый API), LibreOffice (Docker).

---

## РАЗДЕЛ 1. АРХИТЕКТУРА И ГРАНИЦЫ СЛОЁВ

### 1.1 End-to-end поток данных

```
[input_prompt/brief.json] + [template.pptx]
        │
        ▼
┌────────────────────────────────────────────────────────────────┐
│ core/parser  (TemplateParser)                                    │
│  - unzip .pptx, парсинг theme1.xml (XML, lxml) -> цвета/шрифты   │
│  - python-pptx: обход slide_layouts -> геометрия плейсхолдеров   │
│  - эвристическая классификация каждого layout (LayoutType)       │
│  => TemplateManifest (кэшируется как <sha256>.manifest.json)     │
└────────────────────────────────────────────────────────────────┘
        │ TemplateManifest
        ▼
┌────────────────────────────────────────────────────────────────┐
│ core/agents  (NarrativeArchitect -> SlideSlotFiller)              │
│  - NarrativeArchitect: brief + manifest.layout_types -> Outline  │
│    (по 1 разу на вариант A/B/C, с разным аксиом-промптом)         │
│  - SlideSlotFiller: Outline[i] + variant + manifest -> SlideIR   │
│    (Structured Output / guided_json к Qwen через vLLM)            │
│  - Pydantic-валидация на выходе; при ValidationError -> retry     │
│    (макс. 2 повтора с ошибкой в промпте)                          │
│  => PresentationIR × 3 (A, B, C)                                  │
└────────────────────────────────────────────────────────────────┘
        │ PresentationIR × 3
        ▼
┌────────────────────────────────────────────────────────────────┐
│ core/builder (PptxBuilder)                                        │
│  - копия template.pptx как основа (сохраняем тему/мастер)         │
│  - для каждого SlideIR: подбор layout по LayoutType из манифеста  │
│  - маппинг компонентов IR -> нативные shapes:                     │
│    Title -> placeholder TITLE; BulletBlock -> text_frame;         │
│    MetricCard -> auto_shape (rounded rect) + 2 текстовых блока;   │
│    TableData -> shapes.add_table; ChartData -> add_chart +        │
│    CategoryChartData                                               │
│  - autofit.autofit_font_size() перед записью текста в run          │
│  => variant_{A,B,C}.pptx (BytesIO -> temp file)                   │
└────────────────────────────────────────────────────────────────┘
        │ .pptx × 3
        ▼
┌────────────────────────────────────────────────────────────────┐
│ core/auditor (двухуровневый аудит)                                │
│  Уровень 1 (детерминированный, чистый Python, без LLM):            │
│    - geometry_audit: AABB-коллизии + overflow за границы слайда   │
│    - contrast_audit: WCAG luminance ratio >= 4.5:1                │
│    - density_audit: <=6 буллетов, <=15 слов/буллет, табл. <=7x5   │
│    - regex: TODO|Lorem ipsum|XXX|заглушка                          │
│  Уровень 2 (семантический, LLM-судья по чек-листу):                 │
│    - Action Title содержит вывод, а не описание                    │
│    - числа в IR не галлюцинированы относительно brief               │
│    - язык слайдов не смешан                                         │
│  - auto_fixable issues -> предаются builder'у на повторный проход  │
│    (например: увеличить autofit-запас, обрезать буллет)             │
│  => AuditReport × 3                                                 │
└────────────────────────────────────────────────────────────────┘
        │ audited .pptx × 3 + AuditReport × 3
        ▼
┌────────────────────────────────────────────────────────────────┐
│ core/exporter                                                       │
│  - LibreOffice headless (soffice --headless --convert-to pdf)      │
│    внутри Docker-контейнера, вызов через subprocess                │
│  - pdf2image (Poppler) -> PNG превью на слайд, для HTML-отчёта      │
│  - простой статический HTML-viewer (карусель <img> по PNG)          │
└────────────────────────────────────────────────────────────────┘
        │
        ▼
result_package/
  variant_A.pptx  variant_A.pdf  variant_A_preview/*.png  audit_A.json
  variant_B.pptx  variant_B.pdf  variant_B_preview/*.png  audit_B.json
  variant_C.pptx  variant_C.pdf  variant_C_preview/*.png  audit_C.json
  viewer.html
```

Бюджет времени (цель <=5 минут на колоду из 3 вариантов):
- parser: ~2–5 с (кэшируется по sha256 шаблона, повторный запрос — 0 с)
- agents: ~60–120 с (3 варианта × NarrativeArchitect + до 15 вызовов SlotFiller на вариант; параллелить через `asyncio.gather` с ограничением конкурентности семафором, т.к. локальный vLLM имеет фиксированную пропускную способность)
- builder: ~5–10 с на вариант (чистый Python, без сети)
- auditor L1: <1 с; L2 (LLM): ~10–20 с на вариант (один batched-вызов на весь outline, не по слайду)
- exporter: ~15–30 с на вариант (LibreOffice — самое узкое место, конвертировать 3 варианта параллельно через `asyncio.gather` + `asyncio.Semaphore` до N воркеров soffice)

### 1.2 Модули и их границы ответственности

**`core/parser`** — единственный модуль, которому разрешено трогать сырой XML шаблона и `python-pptx` слой `slide_layouts`/`slide_masters`. Не знает ничего про LLM, IR или аудит. Вход: путь к `.pptx`. Выход: `TemplateManifest`. Побочный эффект: файл кэша манифеста рядом (`.cache/<sha256>.json`).

**`core/agents`** — единственный модуль с сетевыми вызовами к LLM. Не открывает `.pptx` напрямую и не знает про `python-pptx`. Вход: `TemplateManifest` (только сводка типов layout/слотов, не сырые EMU) + бриф пользователя. Выход: `PresentationIR`. Промпты и параметры генерации живут в `/skills/*.yaml`, доступ через `PromptRegistry.load(skill_id, version)` — Python-код НЕ содержит текстов промптов.

**`core/builder`** — единственный модуль, вызывающий `presentation.save()`/`shapes.add_*`. Вход: `TemplateManifest` + `PresentationIR`. Выход: путь к собранному `.pptx` + карта `slide_index -> {shape_id: BBox}` (нужна аудитору, чтобы не парсить XML заново). Не принимает решений о контенте — только геометрия и рендеринг того, что уже провалидировано в IR.

**`core/auditor`** — принимает собранный `.pptx` (точнее, карту BBox от builder + сам файл для скриншотов при семантической проверке) и исходный `PresentationIR`/`brief`. Не пишет в `.pptx` сам — только формирует `AuditReport` и список `patch`-инструкций, которые применяет **builder** повторным проходом (аудитор не мутирует презентацию напрямую — так граница «кто чинит» остаётся однозначной).

**`core/exporter`** — единственный модуль, знающий про LibreOffice/Poppler/Docker-специфику. Вход: путь к `.pptx`. Выход: путь к `.pdf` + список путей к PNG. Никакой бизнес-логики.

Оркестрация всех пяти модулей — в `app/pipeline/orchestrator.py`, который не содержит доменной логики, только вызовы и `asyncio.gather`.

---

## РАЗДЕЛ 2. КОНТРАКТЫ ДАННЫХ (Pydantic v2)

### 2.1 `app/models/template_manifest.py`

```python
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
    """Абсолютные координаты в EMU (English Metric Units), как в OOXML."""

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
    """Координаты 0..1 относительно размеров слайда — для аудита и промптов LLM."""

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
    major_latin: str = "Calibri"  # заголовки
    minor_latin: str = "Calibri"  # тело


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
        # fallback: любой layout с BODY-плейсхолдером, иначе первый доступный
        for l in self.layouts:
            if l.slot_by_type(PlaceholderType.BODY) is not None:
                return l
        return self.layouts[0]
```

### 2.2 `app/models/presentation_ir.py`

```python
from __future__ import annotations

from typing import Annotated, Literal, Union

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
    def check_series_lengths(self) -> "ChartData":
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
    def check_row_shape(self) -> "TableData":
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
    Union[BulletBlock, MetricCard, ChartData, TableData, ImagePlaceholder],
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
    def check_unique_indices(self) -> "PresentationIR":
        indices = [s.slide_index for s in self.slides]
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate slide_index values")
        return self
```

Замечание: жёсткая Pydantic-валидация (`ValueError`) — это первая линия защиты от нарушений density-требований ТЗ; она отсекает мусор ещё до того, как SlideSlotFiller вернёт результат оркестратору (retry-цикл в `core/agents`, см. §5, Спринт 2).

### 2.3 `app/models/audit_report.py`

```python
from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class IssueType(str, Enum):
    COLLISION = "COLLISION"
    OVERFLOW = "OVERFLOW"
    CONTRAST = "CONTRAST"
    DENSITY_BULLETS = "DENSITY_BULLETS"
    DENSITY_WORDS = "DENSITY_WORDS"
    TABLE_SIZE = "TABLE_SIZE"
    PLACEHOLDER_TEXT = "PLACEHOLDER_TEXT"
    SEMANTIC_WEAK_TITLE = "SEMANTIC_WEAK_TITLE"
    HALLUCINATED_NUMBER = "HALLUCINATED_NUMBER"
    LANGUAGE_MIX = "LANGUAGE_MIX"


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class AuditIssue(BaseModel):
    issue_type: IssueType
    severity: Severity
    slide_index: int
    message: str
    bbox: BBox | None = None
    shape_ids: list[str] = Field(default_factory=list)
    auto_fixable: bool = False
    fixed: bool = False


class AuditReport(BaseModel):
    variant: Literal["A", "B", "C"]
    issues: list[AuditIssue] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> bool:
        return not any(
            i.severity == Severity.CRITICAL and not i.fixed for i in self.issues
        )

    @property
    def critical_unfixed(self) -> list[AuditIssue]:
        return [
            i for i in self.issues if i.severity == Severity.CRITICAL and not i.fixed
        ]
```

---

## РАЗДЕЛ 3. КЛЮЧЕВЫЕ АЛГОРИТМЫ

### 3.1 Auto-fit шрифта (`app/core/builder/autofit.py`)

Бинарный поиск по кеглю: на каждой итерации симулируем перенос строк через `PIL.ImageFont.getlength()` (без реального рендера — быстро) и проверяем, помещается ли итоговая высота текста в bounding box.

```python
from __future__ import annotations

from PIL import ImageFont

EMU_PER_INCH = 914_400


def _wrap_line_count(
    text: str, font: ImageFont.FreeTypeFont, max_width_px: float
) -> int:
    words = text.split() or [""]
    lines = 1
    line_width = 0.0
    space_width = font.getlength(" ")
    for word in words:
        word_width = font.getlength(word)
        if line_width == 0:
            line_width = word_width
        elif line_width + space_width + word_width <= max_width_px:
            line_width += space_width + word_width
        else:
            lines += 1
            line_width = word_width
    return lines


def autofit_font_size(
    paragraphs: list[str],
    box_width_emu: int,
    box_height_emu: int,
    font_path: str,
    min_size_pt: int = 10,
    max_size_pt: int = 44,
    line_spacing: float = 1.2,
    dpi: int = 96,
) -> int:
    """Возвращает наибольший кегль (pt), при котором все paragraphs
    помещаются в box без переполнения по высоте, с учётом переноса строк."""
    box_width_px = box_width_emu / EMU_PER_INCH * dpi
    box_height_px = box_height_emu / EMU_PER_INCH * dpi

    def fits(size_pt: int) -> bool:
        font = ImageFont.truetype(font_path, int(size_pt * dpi / 72))
        total_lines = sum(_wrap_line_count(p, font, box_width_px) for p in paragraphs)
        line_height_px = font.size * line_spacing
        return total_lines * line_height_px <= box_height_px

    if not fits(min_size_pt):
        return min_size_pt  # минимум — не роняем пайплайн, аудитор поймает overflow

    lo, hi, best = min_size_pt, max_size_pt, min_size_pt
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best
```

Требование к шрифту: `font_path` резолвится через `app/core/builder/font_resolver.py`, который пытается найти системный `.ttf`/`.otf` по имени гарнитуры из `TemplateManifest.fonts`, иначе — fallback на `DejaVuSans.ttf` (лежит в Docker-образе, см. §4).

### 3.2 AABB Collision Detection (`app/core/auditor/geometry_audit.py`)

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BBox:
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h


def intersection(a: BBox, b: BBox, tolerance: float = 0.0) -> BBox | None:
    ix1 = max(a.x, b.x) - tolerance
    iy1 = max(a.y, b.y) - tolerance
    ix2 = min(a.x2, b.x2) + tolerance
    iy2 = min(a.y2, b.y2) + tolerance
    if ix1 < ix2 and iy1 < iy2:
        return BBox(ix1, iy1, ix2 - ix1, iy2 - iy1)
    return None


def find_collisions(
    shapes: list[tuple[str, BBox]], tolerance: float = 0.0
) -> list[tuple[str, str, BBox]]:
    """O(n^2) — оправдано: на слайде обычно <15 shapes."""
    out: list[tuple[str, str, BBox]] = []
    for i in range(len(shapes)):
        id_a, box_a = shapes[i]
        for j in range(i + 1, len(shapes)):
            id_b, box_b = shapes[j]
            overlap = intersection(box_a, box_b, tolerance)
            if overlap is not None:
                out.append((id_a, id_b, overlap))
    return out


def is_within_bounds(box: BBox, slide_w: float, slide_h: float) -> bool:
    return box.x >= 0 and box.y >= 0 and box.x2 <= slide_w and box.y2 <= slide_h
```

### 3.3 WCAG Contrast Ratio (`app/core/auditor/contrast_audit.py`)

```python
from __future__ import annotations


def _srgb_to_linear(channel_0_255: int) -> float:
    c = channel_0_255 / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    r_lin, g_lin, b_lin = (_srgb_to_linear(v) for v in (r, g, b))
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    l1, l2 = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_wcag_aa(text_hex: str, bg_hex: str, large_text: bool = False) -> bool:
    threshold = 3.0 if large_text else 4.5
    return contrast_ratio(text_hex, bg_hex) >= threshold
```

### 3.4 Theme Color Extractor (`app/core/parser/theme_extractor.py`)

```python
from __future__ import annotations

from lxml import etree

NSMAP = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

FALLBACK_COLORS: dict[str, str] = {
    "dk1": "000000",
    "lt1": "FFFFFF",
    "dk2": "44546A",
    "lt2": "E7E6E6",
    "accent1": "4472C4",
    "accent2": "ED7D31",
    "accent3": "A5A5A5",
    "accent4": "FFC000",
    "accent5": "5B9BD5",
    "accent6": "70AD47",
    "hlink": "0563C1",
    "fol_hlink": "954F72",
}

_XML_TO_OUT_KEY = {
    "dk1": "dk1",
    "lt1": "lt1",
    "dk2": "dk2",
    "lt2": "lt2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "fol_hlink",
}


def extract_theme_colors(theme_xml_bytes: bytes) -> dict[str, str]:
    """Извлекает clrScheme из ppt/theme/themeN.xml. Безопасно к отсутствию
    узлов (кастомные/битые темы) — всегда возвращает полный набор ключей."""
    result = dict(FALLBACK_COLORS)
    try:
        root = etree.fromstring(theme_xml_bytes)
    except etree.XMLSyntaxError:
        return result

    scheme = root.find(".//a:clrScheme", NSMAP)
    if scheme is None:
        return result

    for xml_key, out_key in _XML_TO_OUT_KEY.items():
        node = scheme.find(f"a:{xml_key}", NSMAP)
        if node is None:
            continue
        srgb = node.find("a:srgbClr", NSMAP)
        if srgb is not None and srgb.get("val"):
            result[out_key] = srgb.get("val").upper()
            continue
        sys_clr = node.find("a:sysClr", NSMAP)
        if sys_clr is not None and sys_clr.get("lastClr"):
            result[out_key] = sys_clr.get("lastClr").upper()
        # иначе — оставляем fallback для этого ключа
    return result


def extract_font_scheme(theme_xml_bytes: bytes) -> tuple[str, str]:
    """Возвращает (major_latin, minor_latin). Fallback -> ('Calibri', 'Calibri')."""
    try:
        root = etree.fromstring(theme_xml_bytes)
    except etree.XMLSyntaxError:
        return "Calibri", "Calibri"

    major = root.find(".//a:fontScheme/a:majorFont/a:latin", NSMAP)
    minor = root.find(".//a:fontScheme/a:minorFont/a:latin", NSMAP)
    major_name = major.get("typeface") if major is not None else None
    minor_name = minor.get("typeface") if minor is not None else None
    return (major_name or "Calibri"), (minor_name or "Calibri")
```

---

## РАЗДЕЛ 4. СТРУКТУРА ПРОЕКТА

```
LCT 09.26/
├── pyproject.toml
├── uv.lock
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── ARCHITECTURE.md                 # этот документ
├── main.py                         # локальный dev-энтрипоинт (uvicorn reload)
├── app/
│   ├── __init__.py
│   ├── config.py                   # Settings (pydantic-settings): LLM_BASE_URL, LLM_MODEL, ...
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI() + роуты + startup
│   │   ├── routes.py               # POST /generate, GET /jobs/{id}, GET /jobs/{id}/files/{name}
│   │   └── schemas.py              # GenerateRequest/JobStatusResponse (API-DTO, не IR)
│   ├── core/
│   │   ├── parser/
│   │   │   ├── __init__.py
│   │   │   ├── template_parser.py      # TemplateParser.parse(path) -> TemplateManifest
│   │   │   ├── theme_extractor.py      # см. §3.4
│   │   │   ├── font_resolver.py        # имя гарнитуры -> путь к .ttf на диске
│   │   │   └── layout_classifier.py    # LayoutManifest эвристика -> LayoutType
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── llm_client.py           # обёртка над OpenAI-совместимым API + guided_json
│   │   │   ├── prompt_registry.py      # PromptRegistry.load(skill_id, version) из /skills
│   │   │   ├── narrative_architect.py  # brief -> Outline
│   │   │   └── slot_filler.py          # OutlineItem -> SlideIR (с retry на ValidationError)
│   │   ├── builder/
│   │   │   ├── __init__.py
│   │   │   ├── pptx_builder.py         # PresentationIR -> .pptx, возвращает BBox-карту
│   │   │   ├── autofit.py              # см. §3.1
│   │   │   └── shape_factory.py        # per-component фабрики shapes
│   │   ├── auditor/
│   │   │   ├── __init__.py
│   │   │   ├── geometry_audit.py       # см. §3.2
│   │   │   ├── contrast_audit.py       # см. §3.3
│   │   │   ├── density_audit.py        # regex-заглушки + повторная проверка density
│   │   │   ├── semantic_audit.py       # LLM-судья по чек-листу
│   │   │   └── audit_runner.py         # запускает L1+L2, собирает AuditReport
│   │   └── exporter/
│   │       ├── __init__.py
│   │       ├── pdf_exporter.py         # subprocess soffice --headless --convert-to pdf
│   │       └── preview_renderer.py     # pdf2image -> PNG
│   ├── models/
│   │   ├── __init__.py
│   │   ├── template_manifest.py        # см. §2.1
│   │   ├── presentation_ir.py          # см. §2.2
│   │   ├── outline.py                  # Outline/OutlineItem (промежуточная модель агентов)
│   │   └── audit_report.py             # см. §2.3
│   └── pipeline/
│       ├── __init__.py
│       ├── orchestrator.py             # generate_deck(brief, template_path) -> ResultPackage
│       └── jobs.py                     # in-memory job store (dict), статусы PENDING/RUNNING/DONE/FAILED
├── skills/                             # версионированные промпты — НЕ в Python-коде
│   ├── narrative_architect/
│   │   ├── v1.yaml
│   │   └── CHANGELOG.md
│   ├── slot_filler/
│   │   └── v1.yaml
│   └── semantic_audit/
│       └── v1.yaml
├── tests/
│   ├── unit/
│   │   ├── test_theme_extractor.py
│   │   ├── test_layout_classifier.py
│   │   ├── test_autofit.py
│   │   ├── test_geometry_audit.py
│   │   ├── test_contrast_audit.py
│   │   ├── test_presentation_ir_validation.py
│   │   └── test_audit_runner.py
│   ├── integration/
│   │   ├── test_template_parser_roundtrip.py
│   │   ├── test_pptx_builder_roundtrip.py
│   │   └── test_full_pipeline.py
│   ├── fixtures/
│   │   └── templates/
│   │       ├── corporate_minimal.pptx
│   │       └── corporate_dense.pptx
│   └── AUDIT.md                        # матрица трассировки: пункт ТЗ -> тест
├── scripts/
│   └── run_local.ps1
└── docker/
    └── fonts/                          # DejaVuSans + Liberation (fallback для autofit)
```

### 4.1 Dockerfile

```dockerfile
FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress \
    poppler-utils \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 4.2 docker-compose.yml

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - LLM_BASE_URL=${LLM_BASE_URL:-http://host.docker.internal:8001/v1}
      - LLM_MODEL=${LLM_MODEL:-Qwen2.5-32B-Instruct}
    volumes:
      - ./storage:/app/storage
```

### 4.3 Пример версионированного промпта — `skills/narrative_architect/v1.yaml`

```yaml
id: narrative_architect
version: 1
model_params:
  temperature: 0.4
  max_tokens: 2048
system_prompt: |
  Ты — Narrative Architect, эксперт по структуре деловых презентаций.
  Твоя задача — построить план (outline) презентации из брифа пользователя,
  используя ТОЛЬКО перечисленные типы макетов. Не придумывай данные —
  используй только факты, явно присутствующие в брифе.
user_template: |
  БРИФ:
  {brief}

  ОСЬ ВАРИАНТА: {variant_description}
  ЦЕЛЕВОЕ ЧИСЛО СЛАЙДОВ: {n_slides_min}-{n_slides_max}
  ДОСТУПНЫЕ ТИПЫ МАКЕТОВ: {available_layout_types}

  Верни JSON строго по схеме Outline.
output_schema_ref: app.models.outline.Outline
```

`variant_description` для трёх осей (эти строки тоже вынесены в yaml, не в код):
- A (Executive): `"Крупные KPI и тезисы. Минимум буллетов. Каждый слайд — один ключевой вывод."`
- B (Analytical): `"Приоритет нативным графикам (ChartData) и сравнительным таблицам (TableData) над текстом."`
- C (Structural/Process): `"Приоритет пошаговым карточкам, таймлайнам и структурированным спискам (LayoutType.PROCESS_TIMELINE)."`

`tests/AUDIT.md` — таблица «пункт ТЗ → тест», например:

| Требование ТЗ | Тест |
|---|---|
| Макс. 6 буллетов | `tests/unit/test_presentation_ir_validation.py::test_bullet_block_rejects_seventh_item` |
| Контраст WCAG >= 4.5:1 | `tests/unit/test_contrast_audit.py::test_check_wcag_aa_boundary` |
| Нет заглушек TODO/Lorem ipsum | `tests/unit/test_audit_runner.py::test_placeholder_regex_detected` |
| Таблица <=7×5 | `tests/unit/test_presentation_ir_validation.py::test_table_rejects_oversize` |
| Время генерации <=5 мин | `tests/integration/test_full_pipeline.py::test_pipeline_completes_under_timeout` |

---

## РАЗДЕЛ 5. ПОШАГОВЫЙ БЭКЛОГ (5 спринтов)

Каждая задача — самодостаточный промпт для Claude Coder: входные/выходные типы, контракт, edge cases, минимальный pytest.

---

### СПРИНТ 1 — Parser: разбор шаблона в `TemplateManifest`

**Файл:** [app/models/template_manifest.py](app/models/template_manifest.py)
> Реализуй Pydantic v2 модели `PlaceholderType`, `LayoutType`, `Geometry`, `NormalizedGeometry`, `LayoutSlot`, `LayoutManifest`, `ThemeColors`, `FontScheme`, `TemplateManifest` в точности как в §2.1 этого документа. Добавь методы `find_layout` и `find_layout_or_fallback`. Никакой логики парсинга здесь быть не должно — только структуры данных и их инварианты (валидаторы hex-цвета).
> Тест: `tests/unit/test_template_manifest_model.py` — создать `ThemeColors(dk1="bad", ...)` и убедиться, что поднимается `pydantic.ValidationError`.

**Файл:** [app/core/parser/theme_extractor.py](app/core/parser/theme_extractor.py)
> Реализуй `extract_theme_colors(theme_xml_bytes: bytes) -> dict[str, str]` и `extract_font_scheme(theme_xml_bytes: bytes) -> tuple[str, str]` в точности как в §3.4.
> Edge cases: (1) XML без `clrScheme` вообще — вернуть `FALLBACK_COLORS` целиком; (2) `sysClr` без `lastClr` — оставить fallback для этого конкретного ключа, не падать; (3) битый XML (`XMLSyntaxError`) — вернуть fallback, не бросать исключение наверх.
> Тест: `tests/unit/test_theme_extractor.py::test_extract_theme_colors_missing_scheme_returns_fallback` — подать `b"<a:theme xmlns:a='...'/>"` без `clrScheme`, проверить `result == FALLBACK_COLORS`.

**Файл:** [app/core/parser/layout_classifier.py](app/core/parser/layout_classifier.py)
> Реализуй `classify_layout(layout_name: str, slots: list[LayoutSlot]) -> LayoutType`. Эвристика в 2 прохода:
> 1. По имени layout (case-insensitive substring match на русские/английские алиасы): `"title"/"титул"` → `TITLE_SLIDE`, `"section"/"раздел"` → `SECTION_HEADER`, `"table"/"таблиц"` → `TABLE_FOCUSED`, `"chart"/"график"/"диаграмм"` → `CHART_FOCUSED`, `"comparison"/"сравнен"` → `COMPARISON`, `"two content"/"2 колон"` → `CONTENT_2COL`, `"blank"/"пуст"` → `BLANK`.
> 2. Если имя не дало результата — по составу `slots`: ровно `TITLE`+`SUBTITLE` и это единственные два слота → `TITLE_SLIDE`; есть `TABLE` → `TABLE_FOCUSED`; есть `CHART` → `CHART_FOCUSED`; два и более `BODY` с непересекающимися `normalized.x` диапазонами → `CONTENT_2COL`; один `BODY` → `CONTENT_1COL`; иначе → `UNKNOWN`.
> Edge case: пустой список `slots` → `BLANK`. Порядок эвристик фиксирован — сначала имя, потом геометрия, чтобы кастомные шаблоны с нестандартными названиями (у которых, тем не менее, вменяемая геометрия) не проваливались в `UNKNOWN`.
> Тест: `tests/unit/test_layout_classifier.py::test_classify_by_slot_composition_when_name_uninformative` — `layout_name="Custom 7"`, `slots=[TITLE, BODY×2 side-by-side]` → `CONTENT_2COL`.

**Файл:** [app/core/parser/font_resolver.py](app/core/parser/font_resolver.py)
> `resolve_font_path(typeface_name: str, fallback_dir: str = "docker/fonts") -> str`. Ищет `.ttf`/`.otf` в системных каталогах шрифтов (`matplotlib.font_manager.findfont` как基础, либо ручной обход `C:/Windows/Fonts` и `/usr/share/fonts` в зависимости от `platform.system()`), при отсутствии — возвращает путь к `DejaVuSans.ttf` из `fallback_dir`.
> Edge case: `typeface_name=""` или `None` → сразу fallback, без попытки резолва.
> Тест: `tests/unit/test_font_resolver.py::test_unknown_typeface_returns_fallback` — `resolve_font_path("NonExistentFont9000")` заканчивается на `DejaVuSans.ttf` и файл по этому пути существует.

**Файл:** [app/core/parser/template_parser.py](app/core/parser/template_parser.py)
> `class TemplateParser: def parse(self, pptx_path: str) -> TemplateManifest`. Открывает файл через `python-pptx.Presentation`, для каждого `slide_layout` в `prs.slide_layouts` собирает `LayoutSlot` из `layout.placeholders` (координаты — если `placeholder.left is None`, наследовать от `placeholder.element` через мастер; если и там `None` — пропустить слот, не падать). Достаёт `theme1.xml` напрямую из zip-архива `.pptx` (через `zipfile.ZipFile(pptx_path)`, путь `ppt/theme/theme1.xml`) и передаёт байты в `theme_extractor`. Кэширует результат: `sha256(file_bytes)` → `.cache/{hash}.manifest.json`; при повторном вызове с тем же хэшем читает кэш вместо повторного парсинга.
> Edge cases: (1) `.pptx` без явной темы (пустой `theme1.xml` или отсутствует) → `theme_extractor` вернёт fallback, `TemplateParser` не падает; (2) layout без единого текстового плейсхолдера (только `PICTURE`) → `LayoutManifest.slots` может быть пустым списком, это валидно; (3) повреждённый `.pptx` (не zip) → поднять явное `TemplateParseError` с понятным сообщением, а не голый `BadZipFile`.
> Тест: `tests/integration/test_template_parser_roundtrip.py::test_parse_produces_valid_manifest` — распарсить `tests/fixtures/templates/corporate_minimal.pptx`, проверить `manifest.layouts` непустой и хотя бы один layout имеет `layout_type == LayoutType.TITLE_SLIDE`.

---

### СПРИНТ 2 — IR-модели и LLM-агенты

**Файл:** [app/models/presentation_ir.py](app/models/presentation_ir.py)
> Реализуй все модели из §2.2 дословно (`TitleComponent`, `BulletItem`, `BulletBlock`, `MetricCard`, `ChartSeries`, `ChartData`, `TableData`, `ImagePlaceholder`, `SlideComponent` discriminated union, `SlideIR`, `PresentationIR`).
> Тест: `tests/unit/test_presentation_ir_validation.py` — минимум 4 кейса: (a) `BulletBlock` с 7 items → `ValidationError`; (b) `BulletItem(text="16 слов ...")` → `ValidationError` по count; (c) `ChartData` с несовпадающей длиной `series.values` и `categories` → `ValidationError`; (d) `TableData` со строкой другой длины, чем `headers` → `ValidationError`.

**Файл:** [app/models/outline.py](app/models/outline.py)
> `class OutlineItem(BaseModel): slide_index: int; working_title: str; key_message: str; suggested_layout_type: LayoutType; content_hint: str`. `class Outline(BaseModel): variant: Literal["A","B","C"]; items: list[OutlineItem] = Field(min_length=10, max_length=15)`.
> Тест: `test_outline_rejects_out_of_range_length` — 16 items → `ValidationError`.

**Файл:** [app/core/agents/prompt_registry.py](app/core/agents/prompt_registry.py)
> `class PromptSpec(BaseModel): id: str; version: int; model_params: dict; system_prompt: str; user_template: str; output_schema_ref: str`. `class PromptRegistry: def load(self, skill_id: str, version: int | Literal["latest"] = "latest") -> PromptSpec` — читает `skills/{skill_id}/v{version}.yaml` через `yaml.safe_load`; `"latest"` резолвится сканированием `skills/{skill_id}/v*.yaml` и выбором максимального номера.
> Edge case: запрошенная версия отсутствует → `FileNotFoundError` с явным сообщением (не молчаливый fallback на другую версию — это версия промпта, тихая подмена недопустима).
> Тест: `tests/unit/test_prompt_registry.py::test_load_latest_picks_highest_version` — создать `v1.yaml` и `v2.yaml` во временной директории, убедиться что `load("x", "latest")` вернул `version == 2`.

**Файл:** [app/core/agents/llm_client.py](app/core/agents/llm_client.py)
> `class LLMClient` — тонкая обёртка над `openai.AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key="EMPTY")`. Метод `async def complete_structured(self, system_prompt: str, user_prompt: str, response_model: type[BaseModel], model_params: dict) -> BaseModel`: вызывает `chat.completions.create(..., extra_body={"guided_json": response_model.model_json_schema()})`, парсит `response.choices[0].message.content` через `response_model.model_validate_json`. При `pydantic.ValidationError` — не глотать, пробрасывать наверх (retry — ответственность вызывающего агента, не клиента).
> Edge case: LLM вернул текст с markdown-обёрткой ```` ```json ... ``` ````— перед валидацией срезать fence через `re.sub(r"^```(json)?|```$", "", content.strip())`.
> Тест: `tests/unit/test_llm_client.py::test_strips_markdown_fence_before_validation` — замокать `AsyncOpenAI` (через `unittest.mock`), вернуть контент с ```` ```json{"a":1}``` ````, проверить что парсинг простой pydantic-модели `{a: int}` проходит.

**Файл:** [app/core/agents/narrative_architect.py](app/core/agents/narrative_architect.py)
> `async def build_outline(brief: str, manifest: TemplateManifest, variant: Literal["A","B","C"], llm: LLMClient, registry: PromptRegistry) -> Outline`. Берёт `variant_description` по словарю в §4.3, рендерит `user_template` через `.format()`, вызывает `llm.complete_structured(..., response_model=Outline)`.
> Edge case: если у `manifest` нет ни одного layout, кроме `UNKNOWN`/`BLANK` — всё равно передать их в промпт как «доступные типы» (агент обязан работать с любым, даже бедным, шаблоном — п.1 бизнес-ограничений).
> Тест: `tests/unit/test_narrative_architect.py::test_build_outline_calls_llm_with_variant_description` — замокать `LLMClient.complete_structured`, проверить что в `user_prompt` присутствует строка про "Analytical" при `variant="B"`.

**Файл:** [app/core/agents/slot_filler.py](app/core/agents/slot_filler.py)
> `async def fill_slide(item: OutlineItem, variant: str, manifest: TemplateManifest, brief: str, llm: LLMClient, registry: PromptRegistry, max_retries: int = 2) -> SlideIR`. Формирует промпт с `item.content_hint` + допустимыми типами компонентов для `item.suggested_layout_type` (например, для `TABLE_FOCUSED` — обязательно включить `TableData`). При `ValidationError` от Pydantic — на повторной попытке добавляет в `user_prompt` текст ошибки (`str(exc)`) и явную инструкцию исправить. После `max_retries` неудачных попыток — поднимает `SlotFillError(item.slide_index, last_exception)` (не молчаливая деградация — оркестратор должен решить, что делать с упавшим слайдом).
> Edge case: `suggested_layout_type` отсутствует в `manifest.layouts` (кастомный шаблон беднее ожиданий) → `slot_filler` берёт `manifest.find_layout_or_fallback(...)` и подставляет фактически найденный `layout_type` в итоговый `SlideIR.layout_type` (не тот, что предложил Outline).
> Тест: `tests/unit/test_slot_filler.py::test_retries_on_validation_error_then_succeeds` — замокать `llm.complete_structured` так, чтобы первый вызов вернул объект с 8 буллетами (сам объект как dict, конструирование `BulletBlock` из него должно упасть), второй — валидный; убедиться что итоговый вызов успешен и `llm.complete_structured` вызван дважды.

---

### СПРИНТ 3 — Builder: сборка нативного `.pptx`

**Файл:** [app/core/builder/shape_factory.py](app/core/builder/shape_factory.py)
> Набор функций `render_bullet_block(text_frame, block: BulletBlock, font_name: str, base_color: str) -> None`, `render_metric_card(slide, geometry: Geometry, card: MetricCard, theme: ThemeColors) -> BBox` (рисует `add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, ...)` + два `add_textbox` внутри — label и value), `render_table(slide, geometry: Geometry, table: TableData) -> BBox` (через `slide.shapes.add_table(rows, cols, left, top, width, height)`), `render_chart(slide, geometry: Geometry, chart: ChartData) -> BBox` (через `CategoryChartData()` + `slide.shapes.add_chart(XL_CHART_TYPE.<mapped>, ...)`, маппинг `chart_type` строки на `XL_CHART_TYPE` enum: `bar→BAR_CLUSTERED, column→COLUMN_CLUSTERED, line→LINE, pie→PIE`).
> Каждая `render_*` возвращает фактический `BBox` (в EMU) добавленного shape — нужен для карты аудита.
> Edge case: `TableData` с 1 строкой заголовков и 0 data rows — `Pydantic` уже запрещает (`rows` min_length=1), но `shape_factory` не должен на это полагаться повторной проверкой — просто рендерит то, что получил.
> Тест: `tests/unit/test_shape_factory.py::test_render_chart_maps_pie_type` — вызвать `render_chart` с `chart_type="pie"` на реальном `python-pptx.Presentation().slides[...]`, проверить `shape.chart.chart_type == XL_CHART_TYPE.PIE`.

**Файл:** [app/core/builder/pptx_builder.py](app/core/builder/pptx_builder.py)
> `class PptxBuilder: def build(self, template_path: str, manifest: TemplateManifest, ir: PresentationIR) -> BuildResult`, где `BuildResult = namedtuple/dataclass(pptx_path: str, bbox_map: dict[int, dict[str, BBox]])`. Алгоритм: `prs = Presentation(template_path)`; удалить все существующие слайды кроме layouts (`python-pptx` не имеет прямого API удаления слайдов из шаблона — реализовать через `xml_slides.remove(slide.element)` + очистку `sldIdLst`); для каждого `SlideIR` — `layout = manifest.find_layout_or_fallback(slide_ir.layout_type)`, взять реальный `prs.slide_layouts[layout.layout_index]`, `prs.slides.add_slide(pptx_layout)`; заполнить `TITLE`-плейсхолдер текстом с autofit; для каждого `component` в `slide_ir.components` — вызвать соответствующую `render_*` из `shape_factory`, используя следующий свободный `BODY`/`TABLE`/`CHART`-слот из `layout.slots` (если подходящего слота не осталось — разместить компонент в auto-layout зоне под последним использованным слотом, вычисленной как `top = previous.bottom + margin`, а не поверх существующих фигур).
> Edge cases: (1) `PresentationIR` содержит компонент `ImagePlaceholder`, но в `layout.slots` нет `PICTURE` — рендерить серый прямоугольник-заглушку с текстом `alt_text` по центру (не падать, не пропускать компонент молча — это должно быть заметно в audit как `INFO`); (2) `slide_ir.components` пустой список (только заголовок) — валидно, ничего кроме title не рендерится; (3) шаблон, где `manifest.layouts` вообще не содержит layout с нужным типом ни разу — используется `find_layout_or_fallback`, что уже гарантирует не-падение.
> Тест: `tests/integration/test_pptx_builder_roundtrip.py::test_build_produces_openable_pptx_with_correct_slide_count` — собрать `PresentationIR` с 10 слайдами на фикстуре шаблона, открыть результат заново через `Presentation(result.pptx_path)`, проверить `len(prs.slides.__iter__ list) == 10`.

**Файл:** [app/core/builder/autofit.py](app/core/builder/autofit.py) — уже описан в §3.1, реализовать дословно.
> Дополнительно: `def apply_autofit_to_text_frame(text_frame, geometry: Geometry, font_path: str) -> None` — собирает все `paragraph.text` из `text_frame.paragraphs`, вызывает `autofit_font_size`, затем проставляет `run.font.size = Pt(result)` на каждый run.
> Тест: `tests/unit/test_autofit.py::test_long_text_gets_smaller_font_than_short_text` — сравнить результат `autofit_font_size` для короткого и длинного текста в одном и том же box — длинный должен получить кегль `<=` кеглю короткого.

---

### СПРИНТ 4 — Auditor: двухуровневая проверка и авто-фикс

**Файл:** [app/core/auditor/geometry_audit.py](app/core/auditor/geometry_audit.py) — реализовать §3.2 дословно, плюс:
> `def run_geometry_audit(bbox_map: dict[str, BBox], slide_w: float, slide_h: float, slide_index: int) -> list[AuditIssue]` — оборачивает `find_collisions` и `is_within_bounds` в `AuditIssue(issue_type=COLLISION/OVERFLOW, severity=CRITICAL, ...)`.
> Edge case: две фигуры пересекаются на <1% площади каждой (артефакт округления EMU) — использовать порог `tolerance=-0.01*min(w,h)` (отрицательный tolerance сжимает бокс перед проверкой), чтобы не плодить false positive.
> Тест: `tests/unit/test_geometry_audit.py::test_touching_edges_not_flagged_as_collision` — два бокса, у которых `a.x2 == b.x` ровно (касаются, не перекрываются) → `find_collisions` возвращает пустой список.

**Файл:** [app/core/auditor/contrast_audit.py](app/core/auditor/contrast_audit.py) — §3.3 дословно, плюс:
> `def run_contrast_audit(text_color_hex: str, bg_color_hex: str, is_large_text: bool, slide_index: int, shape_id: str) -> AuditIssue | None` — возвращает `None`, если проходит порог, иначе `AuditIssue(issue_type=CONTRAST, severity=CRITICAL, auto_fixable=True, message=f"ratio={ratio:.2f}, need>={threshold}")`.
> Тест: `tests/unit/test_contrast_audit.py::test_check_wcag_aa_boundary` — `contrast_ratio("777777","FFFFFF")` — проверить точное граничное значение известной пары (например, серый `#767676` на белом даёт ratio ≈4.5).

**Файл:** [app/core/auditor/density_audit.py](app/core/auditor/density_audit.py)
> `def run_density_audit(ir: PresentationIR) -> list[AuditIssue]` — так как Pydantic уже не пропускает нарушения на входе, этот аудит проверяет **пост-фактум собранный** `.pptx` (на случай если builder что-то дописал вне IR, например автогенерированный footer) и ищет regex по всем текстам shapes: `re.compile(r"\b(TODO|Lorem ipsum|XXX|заглушка|ТБД)\b", re.IGNORECASE)`.
> Edge case: слово содержит подстроку случайно (например, "Maximum" содержит "xim", не "XXX") — паттерн должен требовать границы слова (`\b...\b`) и точное совпадение токена, а не substring-match.
> Тест: `tests/unit/test_density_audit.py::test_placeholder_regex_ignores_false_positive_substring` — текст `"Maximum efficiency"` не триггерит `PLACEHOLDER_TEXT`; текст `"TODO: fix this"` триггерит.

**Файл:** [app/core/auditor/semantic_audit.py](app/core/auditor/semantic_audit.py)
> `async def run_semantic_audit(ir: PresentationIR, brief: str, llm: LLMClient, registry: PromptRegistry) -> list[AuditIssue]` — один batched-вызов на весь `PresentationIR.slides` (не по слайду — иначе бюджет 5 минут не выдержать). Промпт из `skills/semantic_audit/v1.yaml`, чек-лист: (1) заголовки — Action Title с выводом, а не описанием темы; (2) числа в `MetricCard`/`ChartData`/`TableData` присутствуют (буквально или как разумное производное) в `brief`; (3) все `title.text` и `BulletItem.text` на одном языке. Ответ — строгий JSON-список найденных нарушений, маппится в `AuditIssue(issue_type=SEMANTIC_WEAK_TITLE|HALLUCINATED_NUMBER|LANGUAGE_MIX, severity=WARNING, auto_fixable=False)`.
> Edge case: LLM возвращает пустой список (всё ок) — валидный результат, не ошибка. Если LLM-вызов упал (таймаут/сеть) — semantic audit деградирует до `severity=INFO` записи "semantic audit skipped: <reason>", не блокирует пайплайн (сематика — не критична для выпуска, в отличие от L1).
> Тест: `tests/unit/test_semantic_audit.py::test_llm_failure_degrades_to_info_not_exception` — замокать `llm.complete_structured` так, чтобы бросало `TimeoutError`; убедиться `run_semantic_audit` не пробрасывает исключение и возвращает список с одним `INFO`-issue.

**Файл:** [app/core/auditor/audit_runner.py](app/core/auditor/audit_runner.py)
> `async def run_full_audit(build_result: BuildResult, ir: PresentationIR, manifest: TemplateManifest, brief: str, llm: LLMClient, registry: PromptRegistry) -> AuditReport` — последовательно вызывает geometry (по каждому слайду из `bbox_map`), contrast (для каждого текстового shape против цвета фона layout'а из `manifest.colors`), density, затем `await run_semantic_audit(...)`; собирает всё в единый `AuditReport(variant=ir.variant, issues=[...])`.
> Тест: `tests/integration/test_audit_runner_end_to_end.py::test_clean_deck_passes` — собрать заведомо валидный `PresentationIR` (без коллизий, контрастные цвета, короткие буллеты), прогнать через builder + audit_runner, убедиться `report.passed is True`.

---

### СПРИНТ 5 — Exporter, Orchestrator, API, Docker, e2e

**Файл:** [app/core/exporter/pdf_exporter.py](app/core/exporter/pdf_exporter.py)
> `async def convert_to_pdf(pptx_path: str, output_dir: str, timeout_s: int = 60) -> str` — `asyncio.create_subprocess_exec("soffice", "--headless", "--convert-to", "pdf", "--outdir", output_dir, pptx_path)`, ждёт с `asyncio.wait_for(timeout=timeout_s)`. При таймауте — `proc.kill()` и поднять `ExportTimeoutError`.
> Edge case: LibreOffice иногда не создаёт lock-конфликт при параллельном запуске нескольких инстансов на одной user-profile директории — передавать уникальный `-env:UserInstallation=file:///tmp/lo_profile_{uuid}` на каждый вызов, чтобы 3 варианта конвертировались параллельно без гонки.
> Тест (integration, требует установленный LibreOffice — маркировать `@pytest.mark.docker`): `tests/integration/test_pdf_exporter.py::test_convert_produces_pdf_file` — реальная конвертация фикстуры, проверить что файл `.pdf` создан и `size > 0`.

**Файл:** [app/core/exporter/preview_renderer.py](app/core/exporter/preview_renderer.py)
> `def render_previews(pdf_path: str, output_dir: str, dpi: int = 96) -> list[str]` — `pdf2image.convert_from_path(pdf_path, dpi=dpi, output_folder=output_dir, fmt="png", paths_only=True)`.
> Edge case: пустой/повреждённый PDF (0 страниц) — вернуть пустой список, не бросать исключение (вызывающий код в оркестраторе логирует warning и продолжает — превью не критичны для выдачи `.pptx`).
> Тест: `tests/unit/test_preview_renderer.py::test_empty_pdf_returns_empty_list` — сгенерировать пустой PDF через `reportlab`/минимальный valid PDF без страниц, проверить `render_previews` не падает.

**Файл:** [app/pipeline/orchestrator.py](app/pipeline/orchestrator.py)
> `async def generate_deck(brief: str, template_path: str, deps: Dependencies) -> ResultPackage`. Шаги: (1) `manifest = TemplateParser().parse(template_path)`; (2) `outlines = await asyncio.gather(*(build_outline(brief, manifest, v, ...) for v in ("A","B","C")))`; (3) для каждого варианта — `asyncio.gather` по `fill_slide` с `asyncio.Semaphore(concurrency_limit)` (не заваливать vLLM параллельными запросами сверх лимита из `Settings.LLM_MAX_CONCURRENCY`); (4) собрать `PresentationIR` из результатов `fill_slide` (упавшие после retries слайды — пропустить с логированием, презентация не должна падать целиком из-за одного слайда, но количество слайдов не должно уйти ниже 10 — иначе поднять `PipelineError`); (5) `PptxBuilder().build(...)` на вариант; (6) `run_full_audit(...)`; если есть `auto_fixable` CRITICAL issues — один повторный проход builder с патчами (например, уменьшенный шрифт на конкретном shape) и повторный geometry+contrast audit (без повторного семантического — дорого); (7) параллельно (`asyncio.gather`) `convert_to_pdf` + `render_previews` на все 3 варианта; (8) собрать `ResultPackage`.
> Обернуть весь `generate_deck` в `asyncio.wait_for(timeout=300)` (5 минут по ТЗ) — при превышении поднять `PipelineTimeoutError` с информацией, на каком шаге застряло (использовать `asyncio.wait` с промежуточными чекпоинтами времени, логировать elapsed на каждом шаге).
> Edge case: брифом является пустая строка — не валиден на уровне API (`app/api/schemas.py` должен требовать `min_length` на `brief`), оркестратор эту защиту не дублирует.
> Тест: `tests/integration/test_full_pipeline.py::test_pipeline_completes_under_timeout` — с замоканным `LLMClient` (детерминированные фикстурные ответы вместо реального Qwen, чтобы тест был быстрым и офлайн) прогнать полный `generate_deck` и проверить `elapsed < 300` и все 3 `.pptx` физически существуют.

**Файл:** [app/api/schemas.py](app/api/schemas.py)
> `class GenerateRequest(BaseModel): brief: str = Field(min_length=10, max_length=5000)`. `class JobStatusResponse(BaseModel): job_id: str; status: Literal["PENDING","RUNNING","DONE","FAILED"]; result: ResultPackageDTO | None; error: str | None`.

**Файл:** [app/api/routes.py](app/api/routes.py)
> `POST /generate` (multipart: `template` файл + `brief` строка) → создаёт job, запускает `generate_deck` в `asyncio.create_task` (не блокируя ответ), возвращает `{"job_id": ...}` с `202 Accepted`. `GET /jobs/{job_id}` → `JobStatusResponse`. `GET /jobs/{job_id}/files/{variant}/{kind}` (`kind` ∈ `pptx|pdf|audit`) → `FileResponse`.
> Edge case: загруженный файл не является валидным `.pptx` (не zip / нет `ppt/presentation.xml`) → вернуть `422` с понятным сообщением до постановки job в очередь, не запускать пайплайн вхолостую.
> Тест: `tests/integration/test_api_routes.py::test_generate_rejects_invalid_pptx_upload` — отправить произвольный текстовый файл под видом `.pptx`, ожидать `422`.

**Файл:** [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml) — как в §4.1–4.2, финальная проверка: `docker compose up --build` поднимает контейнер, `curl localhost:8000/docs` отдаёт Swagger UI.
> Тест (ручной, не pytest): после `docker compose up`, выполнить полный сценарий через `POST /generate` с реальным корпоративным `.pptx` и убедиться, что все 3 варианта скачиваются и открываются в PowerPoint без ошибок восстановления файла.

---

## РАЗДЕЛ 6. ФАКТИЧЕСКИЕ ОТКЛОНЕНИЯ СПРИНТА 5 ОТ ЭТОГО ADR

Реализация следует §5 дословно за следующими исключениями, зафиксированными здесь, чтобы документ оставался источником правды:

1. **`app/api/schemas.py`**: `GenerateRequest` как отдельная Pydantic-модель не реализована. `brief` — multipart/form-data поле (`Form(min_length=10, max_length=5000)` прямо в сигнатуре эндпоинта), а не JSON body, поэтому обёртка в BaseModel не даёт валидационных преимуществ и не используется. `JobStatusResponse` отдаёт `variants: list[VariantResultDTO]` (без внутренних путей к файлам) вместо `result: ResultPackageDTO | None` — см. п.2.
2. **`GET /jobs/{job_id}/files/{variant}/{kind}`**: `kind` ∈ `{pptx, pdf}`, без `audit`. Отчёт аудита не отдаётся отдельным файлом — его сводка (`audit_passed: bool | None`) уже присутствует в `JobStatusResponse.variants[i].audit_passed`; полный `AuditReport` остаётся внутренним объектом оркестратора и наружу не сериализуется в этом спринте.
3. **Постановка задачи в фон**: `background_tasks.add_task(...)` (FastAPI `BackgroundTasks`), а не `asyncio.create_task` — эквивалентно по эффекту (не блокирует ответ `202`), но интегрировано со штатным механизмом FastAPI вместо ручного управления задачей.
4. **FastAPI DI**: `Settings` инжектируется как `Annotated[Settings, Depends(get_settings)]`, конкретный паттерн не был закреплён в ADR явно.
5. **`docker-compose.yml`**: используется `env_file: [.env]` вместо `environment:` со списком `${VAR:-default}` из §4.2 — переменные не дублируются в самом compose-файле, единственный источник — `.env` (создаётся из `.env.example`).
6. **Auto-fix проход builder'а по `auto_fixable` issues** (§5, Сприт 5, шаг 6 алгоритма `generate_deck`: "повторный проход builder с патчами") **не реализован** — ни в этом спринте, ни в предыдущих. `AuditIssue.auto_fixable` — это поле в модели данных, но нет кода, который бы читал его и мутировал уже собранный `.pptx`. `run_full_audit` вызывается один раз за вариант; найденные `CRITICAL`-issues (включая помеченные `auto_fixable=True`, например `OVERFLOW`) отражаются в `AuditReport`, но не исправляются автоматически. Это сознательное сужение объёма в пользу соблюдения 5-минутного бюджета и простоты; при необходимости реализовать auto-fix — потребуется отдельное архитектурное решение (кто хранит патчи, как избежать повторного семантического аудита, как ограничить число итераций).
7. **`app/pipeline/orchestrator.py`**: единичный сбой слайда (`SlotFillError`) отбрасывается через `asyncio.gather(..., return_exceptions=True)` + явную фильтрацию по типу исключения — любое **другое** исключение из `fill_slide` пробрасывается наверх и приводит к падению всего варианта (не только слайда), что соответствует бизнес-ограничению «неожиданный баг не должен маскироваться под ожидаемый сбой слайда», но не описано явно в исходном алгоритме §5.
8. **`app/pipeline/orchestrator.py::_build_variant`**: экспорт в PDF (`convert_to_pdf`/`render_previews`) вынесен в собственный `try/except` — сбой LibreOffice (сломанная локальная установка, таймаут) больше не роняет весь вариант целиком. Уже собранный и провалидированный `.pptx` возвращается в `VariantResult` с `pdf_path=None`, `preview_paths=[]`. Причина: PDF/превью — вспомогательный артефакт, а не основной deliverable (см. п.2 бизнес-ограничений — обязателен только нативный `.pptx`), и на реальном железе LibreOffice оказался самым хрупким звеном пайплайна.
9. **`app/core/agents/llm_client.py::complete_structured`**: помимо `extra_body={"guided_json": ...}` (работает только на vLLM/TGI), JSON Schema теперь дополнительно встраивается текстом прямо в system prompt. Причина, подтверждённая на реальном прогоне: при подключении через OpenRouter (и, по всей видимости, любой не-vLLM OpenAI-совместимый бэкенд) `guided_json` тихо игнорируется, и без схемы в тексте модель придумывает собственную структуру JSON (неверные имена полей, отсутствующие обязательные ключи). Это расширяет применимость системы за пределы «только vLLM/TGI», как изначально предполагал п.3 бизнес-ограничений.

---

## РАЗДЕЛ 7. API-ЭНДПОИНТЫ — ДОПОЛНЕНИЕ БЭКЕНДА (Sprint 6)

Спринт 5 закрыл только happy-path API (`POST /generate`, `GET /jobs/{id}`, `GET /jobs/{id}/files/{variant}/{kind}`). При реальной эксплуатации (не единичный прогон из CLI-скрипта, а сервис, которым пользуется фронт/жюри) обнажился набор пробелов. Ниже — что было добавлено, и что сознательно оставлено на будущее.

### 7.1 Что было добавлено

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/health` | Liveness-проверка (для Docker healthcheck и быстрой проверки на защите). Не проверяет доступность LLM/LibreOffice — только то, что процесс жив. |
| `GET` | `/jobs` | Список всех задач (без пагинации — in-memory store, объём мал), отсортирован по `created_at` убыв. |
| `DELETE` | `/jobs/{job_id}` | Удаляет задачу из `JobStore` и рекурсивно чистит `storage/{job_id}/` на диске. `204` при успехе, `404` если задачи нет. |
| `GET` | `/jobs/{job_id}/audit/{variant}` | Отдаёт `AuditReport` варианта как JSON напрямую (не файлом) — раньше аудит не был доступен через API вообще. |
| `GET` | `/jobs/{job_id}/download` | Собирает zip-архив в памяти (`variant_{X}.pptx`, `variant_{X}.pdf` если есть, `audit_{X}.json`) по всем вариантам сразу — раньше фронту пришлось бы делать 3+ отдельных запроса. `404`, если по задаче нет вообще ни одного файла. |

Сопутствующие изменения контрактов:
- `app/pipeline/jobs.py::Job` — добавлены поля `created_at: datetime` и `template_filename: str`; `JobStore` — методы `list_all()` и `delete(job_id)`.
- `app/api/schemas.py` — добавлены `HealthResponse`, `JobSummaryDTO`.
- `app/config.py::Settings` — добавлены `max_upload_bytes: int = 20_000_000` (лимит на загружаемый `.pptx`, раньше отсутствовал вовсе — `UploadFile` ничем не ограничен) и `cors_allow_origins: list[str] = ["*"]`.
- `app/api/main.py` — добавлен `CORSMiddleware` (без него фронт на отдельном origin не сможет достучаться до API из браузера).

### 7.2 Что сознательно НЕ сделано (осталось в объёме хакатона как есть)

- **Аутентификация** — не нужна для закрытого демо-стенда жюри; добавлять смысла нет.
- **Персистентный job store** (Redis/SQLite вместо `dict` в памяти) — при рестарте процесса все задачи и их статусы теряются. Для 7-дневного хакатона и монолита без микросервисов (п.7 бизнес-ограничений) это осознанный компромисс, но при демо на нестабильном стенде — риск.
- **Очистка `storage/`** — `DELETE /jobs/{id}` есть, но ничего не удаляет автоматически; при долгой эксплуатации диск демо-машины забивается старыми job-директориями.
- **Rate limiting / ограничение параллельных job** — ничего не мешает запустить много одновременных `POST /generate`, каждый из которых потребляет LLM-квоту и до 5 минут CPU на LibreOffice.
- **WebSocket/SSE прогресса** — сейчас только polling `GET /jobs/{id}`, этого достаточно для одной демо-сессии.
- **Стриминговая проверка размера аплоада** — `max_upload_bytes` проверяется уже ПОСЛЕ полного чтения файла в память (`await template.read()`), а не по мере получения потока; настоящий DoS большим файлом это не предотвращает, только typical-case ошибки пользователя.
