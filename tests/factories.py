"""Valid-by-construction builders for the ML pipeline's pydantic models."""

from __future__ import annotations

import re

from app.models.outline import Outline, OutlineItem
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    ComparisonData,
    IconListData,
    IconListItem,
    ImagePlaceholder,
    MetricCard,
    PresentationIR,
    ProcessData,
    ProcessStep,
    SlideComponent,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import (
    BrandProfile,
    FontScheme,
    Geometry,
    LayoutManifest,
    LayoutSlot,
    LayoutType,
    NormalizedGeometry,
    PlaceholderType,
    TemplateManifest,
    ThemeColors,
)

VALID_THEME_COLORS: dict[str, str] = {
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

DEFAULT_SLIDE_W = 12_192_000  # 16:9 EMU
DEFAULT_SLIDE_H = 6_858_000


# --------------------------------------------------------------------------- #
# Template manifest
# --------------------------------------------------------------------------- #


def make_slot(
    *,
    placeholder_idx: int,
    placeholder_type: PlaceholderType,
    x: float,
    y: float,
    w: float,
    h: float,
    slide_w: int = DEFAULT_SLIDE_W,
    slide_h: int = DEFAULT_SLIDE_H,
    name: str = "",
    inferred: bool = False,
) -> LayoutSlot:
    return LayoutSlot(
        placeholder_idx=placeholder_idx,
        placeholder_type=placeholder_type,
        geometry=Geometry(
            left_emu=int(x * slide_w),
            top_emu=int(y * slide_h),
            width_emu=max(1, int(w * slide_w)),
            height_emu=max(1, int(h * slide_h)),
        ),
        normalized=NormalizedGeometry(x=x, y=y, w=w, h=h),
        name=name,
        inferred=inferred,
    )


def make_manifest(
    *,
    layouts: list[LayoutManifest] | None = None,
    slide_w: int = DEFAULT_SLIDE_W,
    slide_h: int = DEFAULT_SLIDE_H,
    brand_profile: BrandProfile | None = None,
    source_hash: str = "fixture-hash",
    colors: ThemeColors | None = None,
    fonts: FontScheme | None = None,
) -> TemplateManifest:
    if layouts is None:
        layouts = [
            LayoutManifest(
                layout_index=0,
                layout_name="Content",
                layout_type=LayoutType.CONTENT_1COL,
                slots=[
                    make_slot(
                        placeholder_idx=0,
                        placeholder_type=PlaceholderType.TITLE,
                        x=0.07,
                        y=0.05,
                        w=0.86,
                        h=0.15,
                        slide_w=slide_w,
                        slide_h=slide_h,
                    ),
                    make_slot(
                        placeholder_idx=1,
                        placeholder_type=PlaceholderType.BODY,
                        x=0.07,
                        y=0.25,
                        w=0.86,
                        h=0.65,
                        slide_w=slide_w,
                        slide_h=slide_h,
                    ),
                ],
            )
        ]
    return TemplateManifest(
        source_hash=source_hash,
        slide_width_emu=slide_w,
        slide_height_emu=slide_h,
        colors=colors or ThemeColors(**VALID_THEME_COLORS),
        fonts=fonts or FontScheme(),
        brand_profile=brand_profile or BrandProfile(),
        layouts=layouts,
    )


def _title_layout(index: int, slide_w: int, slide_h: int) -> LayoutManifest:
    return LayoutManifest(
        layout_index=index,
        layout_name="Title Slide",
        layout_type=LayoutType.TITLE_SLIDE,
        slots=[
            make_slot(
                placeholder_idx=0,
                placeholder_type=PlaceholderType.TITLE,
                x=0.1,
                y=0.35,
                w=0.8,
                h=0.2,
                slide_w=slide_w,
                slide_h=slide_h,
            ),
            make_slot(
                placeholder_idx=1,
                placeholder_type=PlaceholderType.SUBTITLE,
                x=0.1,
                y=0.58,
                w=0.8,
                h=0.1,
                slide_w=slide_w,
                slide_h=slide_h,
            ),
        ],
    )


def symmetric_manifest(
    slide_w: int = DEFAULT_SLIDE_W, slide_h: int = DEFAULT_SLIDE_H
) -> TemplateManifest:
    """TITLE_SLIDE, CONTENT_1COL, CONTENT_2COL, COMPARISON — a well-formed corporate deck."""

    layouts = [
        _title_layout(0, slide_w, slide_h),
        LayoutManifest(
            layout_index=1,
            layout_name="Content 1 Column",
            layout_type=LayoutType.CONTENT_1COL,
            slots=[
                make_slot(
                    placeholder_idx=0,
                    placeholder_type=PlaceholderType.TITLE,
                    x=0.07,
                    y=0.05,
                    w=0.86,
                    h=0.15,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=1,
                    placeholder_type=PlaceholderType.BODY,
                    x=0.07,
                    y=0.25,
                    w=0.86,
                    h=0.65,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
            ],
        ),
        LayoutManifest(
            layout_index=2,
            layout_name="Two Content",
            layout_type=LayoutType.CONTENT_2COL,
            slots=[
                make_slot(
                    placeholder_idx=0,
                    placeholder_type=PlaceholderType.TITLE,
                    x=0.07,
                    y=0.05,
                    w=0.86,
                    h=0.15,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=1,
                    placeholder_type=PlaceholderType.BODY,
                    x=0.07,
                    y=0.25,
                    w=0.4,
                    h=0.65,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=2,
                    placeholder_type=PlaceholderType.BODY,
                    x=0.53,
                    y=0.25,
                    w=0.4,
                    h=0.65,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
            ],
        ),
        LayoutManifest(
            layout_index=3,
            layout_name="Comparison",
            layout_type=LayoutType.COMPARISON,
            slots=[
                make_slot(
                    placeholder_idx=0,
                    placeholder_type=PlaceholderType.TITLE,
                    x=0.07,
                    y=0.05,
                    w=0.86,
                    h=0.15,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=1,
                    placeholder_type=PlaceholderType.BODY,
                    x=0.07,
                    y=0.25,
                    w=0.86,
                    h=0.65,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
            ],
        ),
    ]
    return make_manifest(layouts=layouts, slide_w=slide_w, slide_h=slide_h)


def poor_manifest(
    slide_w: int = DEFAULT_SLIDE_W, slide_h: int = DEFAULT_SLIDE_H
) -> TemplateManifest:
    """Only TITLE_SLIDE + UNKNOWN — mirrors a weak real-world corporate template."""

    layouts = [
        _title_layout(0, slide_w, slide_h),
        LayoutManifest(
            layout_index=1,
            layout_name="Custom Layout 7",
            layout_type=LayoutType.UNKNOWN,
            slots=[
                make_slot(
                    placeholder_idx=0,
                    placeholder_type=PlaceholderType.OTHER,
                    x=0.1,
                    y=0.1,
                    w=0.8,
                    h=0.8,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
            ],
        ),
    ]
    return make_manifest(layouts=layouts, slide_w=slide_w, slide_h=slide_h)


def asymmetric_manifest(
    slide_w: int = DEFAULT_SLIDE_W, slide_h: int = DEFAULT_SLIDE_H
) -> TemplateManifest:
    """Content slots confined to the left 45%; a PICTURE ("brand artwork") slot on the right."""

    layouts = [
        _title_layout(0, slide_w, slide_h),
        LayoutManifest(
            layout_index=1,
            layout_name="Content Left",
            layout_type=LayoutType.CONTENT_1COL,
            slots=[
                make_slot(
                    placeholder_idx=0,
                    placeholder_type=PlaceholderType.TITLE,
                    x=0.05,
                    y=0.05,
                    w=0.4,
                    h=0.15,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=1,
                    placeholder_type=PlaceholderType.BODY,
                    x=0.05,
                    y=0.25,
                    w=0.4,
                    h=0.65,
                    slide_w=slide_w,
                    slide_h=slide_h,
                ),
                make_slot(
                    placeholder_idx=2,
                    placeholder_type=PlaceholderType.PICTURE,
                    x=0.5,
                    y=0.0,
                    w=0.5,
                    h=1.0,
                    slide_w=slide_w,
                    slide_h=slide_h,
                    name="brand artwork",
                ),
            ],
        ),
    ]
    return make_manifest(layouts=layouts, slide_w=slide_w, slide_h=slide_h)


# --------------------------------------------------------------------------- #
# Outline
# --------------------------------------------------------------------------- #

_PURPOSES = [
    "context",
    "audience",
    "problem",
    "cause",
    "approach",
    "process",
    "architecture",
    "benefit",
    "risk",
    "decision",
    "roadmap",
    "call to action",
    "governance",
    "economics",
    "next steps",
]

# A 15-slot cycle that (a) never repeats CONTENT_1COL three times in a row and
# (b) always includes at least one COMPARISON and one PROCESS_TIMELINE.
_DEFAULT_LAYOUT_CYCLE: list[LayoutType] = [
    LayoutType.TITLE_SLIDE,
    LayoutType.CONTENT_1COL,
    LayoutType.CONTENT_2COL,
    LayoutType.COMPARISON,
    LayoutType.CONTENT_1COL,
    LayoutType.PROCESS_TIMELINE,
    LayoutType.CONTENT_1COL,
    LayoutType.KPI_DASHBOARD,
    LayoutType.CONTENT_2COL,
    LayoutType.CONTENT_1COL,
    LayoutType.COMPARISON,
    LayoutType.PROCESS_TIMELINE,
    LayoutType.CONTENT_1COL,
    LayoutType.CHART_FOCUSED,
    LayoutType.SECTION_HEADER,
]


def make_outline_item(
    index: int,
    *,
    layout_type: LayoutType = LayoutType.CONTENT_1COL,
    key_message: str | None = None,
    content_hint: str | None = None,
    working_title: str | None = None,
) -> OutlineItem:
    # Deliberately no digits in the generated text: OutlineItem.slide_index
    # is a structural field, but any *number written in the prose* (even a
    # slide position) is treated as a claim by grounding.ungrounded_numbers
    # once it reaches double digits, which would make factory-built outlines
    # spuriously "invent" numbers for slides 10+.
    purpose = _PURPOSES[index % len(_PURPOSES)]
    return OutlineItem(
        slide_index=index,
        working_title=working_title or f"Slide about {purpose}",
        key_message=key_message or f"Distinct {purpose} message point",
        suggested_layout_type=layout_type,
        content_hint=content_hint or f"Concrete source detail about {purpose}",
    )


def make_outline(
    n: int = 12,
    *,
    layout_types: list[LayoutType] | None = None,
    variant: str = "A",
) -> Outline:
    if n < 10 or n > 15:
        raise ValueError("Outline requires between 10 and 15 items")
    cycle = layout_types or _DEFAULT_LAYOUT_CYCLE
    items = [make_outline_item(i, layout_type=cycle[i % len(cycle)]) for i in range(n)]
    return Outline(variant=variant, items=items)


# --------------------------------------------------------------------------- #
# Presentation IR
# --------------------------------------------------------------------------- #


def _default_components_for_layout(layout_type: LayoutType) -> list[SlideComponent]:
    if layout_type == LayoutType.COMPARISON:
        return [
            ComparisonData(
                left_title="До",
                left_items=["Ручной процесс", "Долгое согласование"],
                right_title="После",
                right_items=["Автоматизация", "Быстрое согласование"],
            )
        ]
    if layout_type == LayoutType.PROCESS_TIMELINE:
        return [
            ProcessData(
                steps=[
                    ProcessStep(title="Сбор данных", description="Первый этап"),
                    ProcessStep(title="Анализ", description="Второй этап"),
                    ProcessStep(title="Внедрение", description="Третий этап"),
                ]
            )
        ]
    if layout_type == LayoutType.KPI_DASHBOARD:
        return [
            MetricCard(label="Выручка", value="2,4 млрд ₽", trend="up"),
            MetricCard(label="Конверсия", value="18%", trend="up"),
            MetricCard(label="NPS", value="62", trend="flat"),
        ]
    if layout_type == LayoutType.CHART_FOCUSED:
        return [
            ChartData(
                chart_type="column",
                categories=["Q1", "Q2", "Q3"],
                series=[ChartSeries(name="Выручка", values=[10, 20, 30])],
            )
        ]
    if layout_type == LayoutType.TABLE_FOCUSED:
        return [
            TableData(
                headers=["Метрика", "Значение"],
                rows=[["Магазины", "84"], ["NPS", "62"]],
            )
        ]
    if layout_type == LayoutType.CONTENT_2COL:
        return [
            BulletBlock(items=[BulletItem(text="Левая колонка: первый факт")]),
            BulletBlock(items=[BulletItem(text="Правая колонка: второй факт")]),
        ]
    if layout_type == LayoutType.TITLE_SLIDE:
        return []
    return [
        BulletBlock(
            items=[
                BulletItem(text="Первый подтверждающий пункт"),
                BulletItem(text="Второй подтверждающий пункт"),
            ]
        )
    ]


def make_slide(
    layout_type: LayoutType = LayoutType.CONTENT_1COL,
    *,
    components: list[SlideComponent] | None = None,
    slide_index: int = 0,
    title: str | None = None,
    is_action_title: bool = False,
    speaker_notes: str = "",
) -> SlideIR:
    return SlideIR(
        slide_index=slide_index,
        layout_type=layout_type,
        title=TitleComponent(
            text=title or f"Вывод для слайда {slide_index}",
            is_action_title=is_action_title,
        ),
        components=(
            components
            if components is not None
            else _default_components_for_layout(layout_type)
        ),
        speaker_notes=speaker_notes,
    )


def make_ir(
    n: int = 12,
    *,
    variant: str = "A",
    layout_types: list[LayoutType] | None = None,
    template_source_hash: str = "fixture-hash",
) -> PresentationIR:
    if n < 10 or n > 15:
        raise ValueError("PresentationIR requires between 10 and 15 slides")
    cycle = layout_types or _DEFAULT_LAYOUT_CYCLE
    slides = [
        make_slide(cycle[i % len(cycle)], slide_index=i, title=f"Слайд {i}: вывод")
        for i in range(n)
    ]
    return PresentationIR(
        variant=variant, template_source_hash=template_source_hash, slides=slides
    )


def make_ir_with_all_component_types(
    *, variant: str = "A", template_source_hash: str = "fixture-hash"
) -> PresentationIR:
    """A 10-slide IR that exercises every one of the 8 component types once."""

    slides = [
        make_slide(LayoutType.TITLE_SLIDE, slide_index=0, title="Обложка"),
        make_slide(LayoutType.CONTENT_1COL, slide_index=1, title="Слайд 1: буллеты"),
        make_slide(
            LayoutType.KPI_DASHBOARD,
            slide_index=2,
            title="Слайд 2: метрики",
        ),
        make_slide(
            LayoutType.CHART_FOCUSED,
            slide_index=3,
            title="Слайд 3: график",
        ),
        make_slide(
            LayoutType.TABLE_FOCUSED,
            slide_index=4,
            title="Слайд 4: таблица",
        ),
        make_slide(
            LayoutType.CONTENT_1COL,
            slide_index=5,
            title="Слайд 5: изображение",
            components=[
                ImagePlaceholder(alt_text="Иллюстрация процесса", role="illustration")
            ],
        ),
        make_slide(
            LayoutType.COMPARISON,
            slide_index=6,
            title="Слайд 6: сравнение",
        ),
        make_slide(
            LayoutType.PROCESS_TIMELINE,
            slide_index=7,
            title="Слайд 7: процесс",
        ),
        make_slide(
            LayoutType.CONTENT_1COL,
            slide_index=8,
            title="Слайд 8: список преимуществ",
            components=[
                IconListData(
                    items=[
                        IconListItem(icon="check", title="Быстро"),
                        IconListItem(icon="shield", title="Надёжно"),
                        IconListItem(icon="speed", title="Просто"),
                    ]
                )
            ],
        ),
        make_slide(LayoutType.CONTENT_2COL, slide_index=9, title="Слайд 9: два блока"),
    ]
    return PresentationIR(
        variant=variant, template_source_hash=template_source_hash, slides=slides
    )


# --------------------------------------------------------------------------- #
# Briefs
# --------------------------------------------------------------------------- #


def quality_safe_components(layout_type: LayoutType) -> list[SlideComponent]:
    """Digit-free content that always satisfies slot_filler._slide_quality_problems
    for the given REQUESTED layout, and never trips grounding (no numbers)."""

    if layout_type == LayoutType.KPI_DASHBOARD:
        return [
            MetricCard(label="Выручка", value="Высокая", trend="up"),
            MetricCard(label="Конверсия", value="Растёт", trend="up"),
            MetricCard(label="NPS", value="Стабилен", trend="flat"),
        ]
    if layout_type == LayoutType.CHART_FOCUSED:
        return [
            ChartData(
                chart_type="column",
                categories=["A", "B", "C"],
                series=[ChartSeries(name="S", values=[1, 2, 3])],
            )
        ]
    if layout_type == LayoutType.TABLE_FOCUSED:
        return [TableData(headers=["Метрика", "Значение"], rows=[["Alpha", "Beta"]])]
    if layout_type == LayoutType.CONTENT_2COL:
        return [
            BulletBlock(items=[BulletItem(text="Левая колонка")]),
            BulletBlock(items=[BulletItem(text="Правая колонка")]),
        ]
    if layout_type == LayoutType.COMPARISON:
        return [
            ComparisonData(
                left_title="До",
                left_items=["Ручной процесс"],
                right_title="После",
                right_items=["Автоматизация"],
            )
        ]
    if layout_type == LayoutType.PROCESS_TIMELINE:
        return [
            ProcessData(
                steps=[
                    ProcessStep(title="Шаг один"),
                    ProcessStep(title="Шаг два"),
                    ProcessStep(title="Шаг три"),
                ]
            )
        ]
    return [
        BulletBlock(
            items=[BulletItem(text="Первый пункт"), BulletItem(text="Второй пункт")]
        )
    ]


_SLIDE_INDEX_RE = re.compile(r"slide_index:\s*(\d+)")
_REQUESTED_LAYOUT_RE = re.compile(r"REQUESTED VISUAL COMPOSITION:\s*([A-Z0-9_]+)")
_WORKING_TITLE_RE = re.compile(r"working_title:\s*(.+)")


def adaptive_slide_response(user_prompt: str) -> SlideIR:
    """A ScriptedLLM callable for `SlideIR`: reads the slide_index and the
    requested layout straight out of the slot_filler prompt and returns a
    digit-free, quality-safe slide for it — so a scripted "happy path"
    pipeline run doesn't need to script one fixed response per outline item.

    The title is copied from the outline item's own working_title rather than
    built from the numeric slide_index: any two-digit-or-higher slide index
    written into prose (e.g. "Slide 10") is itself a grounding claim, and a
    brief-less pipeline test has nothing in its brief to ground it against.
    """

    slide_index_match = _SLIDE_INDEX_RE.search(user_prompt)
    slide_index = int(slide_index_match.group(1)) if slide_index_match else 0
    layout_match = _REQUESTED_LAYOUT_RE.search(user_prompt)
    layout_type = (
        LayoutType(layout_match.group(1)) if layout_match else LayoutType.CONTENT_1COL
    )
    title_match = _WORKING_TITLE_RE.search(user_prompt)
    title = (
        title_match.group(1).strip() if title_match else "Содержательный вывод слайда"
    )
    return make_slide(
        layout_type,
        components=quality_safe_components(layout_type),
        slide_index=slide_index,
        title=title,
    )


def grounded_brief() -> str:
    """A Russian brief with specific numbers fake LLM outputs can safely reuse."""

    return (
        "Мы запускаем программу автоматизации складской логистики. "
        "Ожидаемый эффект: выручка сети вырастет до 2,4 млрд ₽ в год. "
        "Конверсия по ключевому сценарию увеличится на 18%. "
        "Среднее время обработки заказа сократится до 45 секунд. "
        "Индекс лояльности клиентов NPS достигнет 62. "
        "Программа охватит 84 магазина сети в первой волне внедрения. "
        "Проект включает четыре этапа: аудит процессов, пилот в 5 магазинах, "
        "тиражирование и последующую поддержку."
    )
