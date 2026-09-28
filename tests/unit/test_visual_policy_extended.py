"""Extended coverage for app/core/agents/visual_policy.py.

Existing tests/unit/test_visual_policy.py keeps the original smoke test;
this file exercises every conversion rule plus a content-invariant check.
"""

from __future__ import annotations

import re

import pytest

from app.core.agents.visual_policy import apply_visual_policy
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    ComparisonData,
    IconListData,
    MetricCard,
    PresentationIR,
    ProcessData,
    SlideIR,
    TitleComponent,
)
from app.models.template_manifest import LayoutType

_WORD_RE = re.compile(r"[а-яёa-z]+", re.IGNORECASE)


def _slide(
    index: int,
    layout_type: LayoutType,
    components,
    title: str | None = None,
) -> SlideIR:
    return SlideIR(
        slide_index=index,
        layout_type=layout_type,
        title=TitleComponent(text=title or f"Вывод {index}"),
        components=components,
    )


def _ir(slides: list[SlideIR], variant: str = "A") -> PresentationIR:
    return PresentationIR(variant=variant, template_source_hash="x", slides=slides)


def _bullets(*texts: str) -> BulletBlock:
    return BulletBlock(items=[BulletItem(text=t) for t in texts])


def _pad_with_neutral_slides(slides: list[SlideIR], target: int = 10) -> list[SlideIR]:
    """Pad out to the PresentationIR minimum with harmless, varied slides that
    never participate in comparison/process conversion or a text-only streak."""
    next_index = max((s.slide_index for s in slides), default=-1) + 1
    padded = list(slides)
    while len(padded) < target:
        padded.append(
            _slide(
                next_index,
                LayoutType.KPI_DASHBOARD,
                [MetricCard(label=f"Метрика {next_index}", value="Значение")],
            )
        )
        next_index += 1
    return padded


# --------------------------------------------------------------------------- #
# COMPARISON conversion
# --------------------------------------------------------------------------- #


def test_comparison_slide_with_two_bullet_blocks_becomes_comparison_data():
    slide = _slide(
        0,
        LayoutType.COMPARISON,
        [
            _bullets("Ручной процесс", "Долгое согласование"),
            _bullets("Автоматизация", "Быстрое согласование"),
        ],
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    converted = result.slides[0].components[0]
    assert isinstance(converted, ComparisonData)
    assert converted.left_title == "До"
    assert converted.right_title == "После"
    assert converted.left_items == ["Ручной процесс", "Долгое согласование"]
    assert converted.right_items == ["Автоматизация", "Быстрое согласование"]


def test_comparison_slide_with_one_bullet_block_is_unchanged():
    slide = _slide(0, LayoutType.COMPARISON, [_bullets("Единственный блок")])
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    assert isinstance(result.slides[0].components[0], BulletBlock)


def test_comparison_slide_with_three_bullet_blocks_is_unchanged():
    slide = _slide(
        0,
        LayoutType.COMPARISON,
        [_bullets("Первый"), _bullets("Второй"), _bullets("Третий")],
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    assert all(isinstance(c, BulletBlock) for c in result.slides[0].components)
    assert len(result.slides[0].components) == 3


# --------------------------------------------------------------------------- #
# PROCESS_TIMELINE conversion
# --------------------------------------------------------------------------- #


def test_process_timeline_with_three_points_becomes_process_data_with_label_split():
    slide = _slide(
        0,
        LayoutType.PROCESS_TIMELINE,
        [
            _bullets(
                "Сбор данных: первый этап",
                "Анализ: второй этап",
                "Внедрение: третий этап",
            )
        ],
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    converted = result.slides[0].components[0]
    assert isinstance(converted, ProcessData)
    assert [s.title for s in converted.steps] == ["Сбор данных", "Анализ", "Внедрение"]
    assert [s.description for s in converted.steps] == [
        "первый этап",
        "второй этап",
        "третий этап",
    ]


def test_process_timeline_point_without_colon_uses_whole_text_as_title():
    slide = _slide(
        0,
        LayoutType.PROCESS_TIMELINE,
        [_bullets("Сбор данных", "Анализ данных", "Внедрение решения")],
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    converted = result.slides[0].components[0]
    assert [s.title for s in converted.steps] == [
        "Сбор данных",
        "Анализ данных",
        "Внедрение решения",
    ]
    assert all(s.description == "" for s in converted.steps)


def test_process_timeline_drops_points_past_six():
    points = [f"Этап {i}: описание {i}" for i in range(8)]
    # MAX_BULLETS caps a single BulletBlock at 6 items, but _process_from_bullets
    # aggregates points across every bullet block on the slide.
    slide = _slide(
        0, LayoutType.PROCESS_TIMELINE, [_bullets(*points[:6]), _bullets(*points[6:])]
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    converted = result.slides[0].components[0]
    assert isinstance(converted, ProcessData)
    assert len(converted.steps) == 6
    assert [s.title for s in converted.steps] == [f"Этап {i}" for i in range(6)]


def test_process_timeline_with_fewer_than_three_points_is_unchanged():
    slide = _slide(
        0, LayoutType.PROCESS_TIMELINE, [_bullets("Только один", "И второй")]
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    assert isinstance(result.slides[0].components[0], BulletBlock)


# --------------------------------------------------------------------------- #
# Text-only streak -> icon_list
# --------------------------------------------------------------------------- #


def test_third_consecutive_bullet_only_slide_becomes_icon_list_and_resets_streak():
    slides = [
        _slide(0, LayoutType.CONTENT_1COL, [_bullets("Раз", "Два")]),
        _slide(1, LayoutType.CONTENT_1COL, [_bullets("Три", "Четыре")]),
        _slide(2, LayoutType.CONTENT_1COL, [_bullets("Пять", "Шесть")]),
        _slide(3, LayoutType.CONTENT_1COL, [_bullets("Семь", "Восемь")]),
    ]
    ir = _ir(_pad_with_neutral_slides(slides))

    result = apply_visual_policy(ir)

    assert isinstance(result.slides[0].components[0], BulletBlock)
    assert isinstance(result.slides[1].components[0], BulletBlock)
    assert isinstance(result.slides[2].components[0], IconListData)
    # streak resets after the conversion, so slide 3 (the 1st slide of a new
    # streak) is not converted again.
    assert isinstance(result.slides[3].components[0], BulletBlock)


def test_non_bullet_slide_resets_the_streak():
    slides = [
        _slide(0, LayoutType.CONTENT_1COL, [_bullets("Раз", "Два")]),
        _slide(1, LayoutType.CONTENT_1COL, [_bullets("Три", "Четыре")]),
        _slide(
            2,
            LayoutType.CHART_FOCUSED,
            [ChartData(categories=["A"], series=[ChartSeries(name="S", values=[1])])],
        ),
        _slide(3, LayoutType.CONTENT_1COL, [_bullets("Пять", "Шесть")]),
        _slide(4, LayoutType.CONTENT_1COL, [_bullets("Семь", "Восемь")]),
    ]
    ir = _ir(_pad_with_neutral_slides(slides))

    result = apply_visual_policy(ir)

    for i in (0, 1, 3, 4):
        assert isinstance(result.slides[i].components[0], BulletBlock)
    assert isinstance(result.slides[2].components[0], ChartData)


# --------------------------------------------------------------------------- #
# Output validity and ordering
# --------------------------------------------------------------------------- #


def test_output_is_always_a_valid_presentation_ir_and_preserves_count_and_order():
    slides = [
        _slide(
            0,
            LayoutType.COMPARISON,
            [_bullets("Было плохо"), _bullets("Стало хорошо")],
        ),
        _slide(
            1, LayoutType.PROCESS_TIMELINE, [_bullets("Шаг раз", "Шаг два", "Шаг три")]
        ),
        _slide(2, LayoutType.CONTENT_1COL, [_bullets("Раз", "Два")]),
        _slide(3, LayoutType.CONTENT_1COL, [_bullets("Три", "Четыре")]),
        _slide(4, LayoutType.CONTENT_1COL, [_bullets("Пять", "Шесть")]),
    ]
    ir = _ir(_pad_with_neutral_slides(slides))

    result = apply_visual_policy(ir)
    revalidated = PresentationIR.model_validate(result.model_dump(mode="json"))

    assert revalidated == result
    assert [s.slide_index for s in result.slides] == [s.slide_index for s in ir.slides]
    assert len(result.slides) == len(ir.slides)


# --------------------------------------------------------------------------- #
# Content invariant (property-style over several generated IRs)
# --------------------------------------------------------------------------- #


def _prose_words(ir: PresentationIR) -> set[str]:
    words: set[str] = set()

    def add(text: str) -> None:
        words.update(w.lower() for w in _WORD_RE.findall(text))

    for slide in ir.slides:
        add(slide.title.text)
        for component in slide.components:
            if isinstance(component, BulletBlock):
                for item in component.items:
                    add(item.text)
            elif isinstance(component, ComparisonData):
                add(component.left_title)
                add(component.right_title)
                for item in [*component.left_items, *component.right_items]:
                    add(item)
            elif component.type == "process":
                for step in component.steps:
                    add(step.title)
                    add(step.description)
            elif component.type == "icon_list":
                for item in component.items:
                    add(item.title)
                    add(item.description)
    return words


def _trial_irs() -> list[PresentationIR]:
    trials = []
    for trial in range(6):
        slides = [
            _slide(
                0,
                LayoutType.COMPARISON,
                [
                    _bullets(f"Риск {trial} А", f"Риск {trial} Б"),
                    _bullets(f"Возможность {trial} А", f"Возможность {trial} Б"),
                ],
            ),
            _slide(
                1,
                LayoutType.PROCESS_TIMELINE,
                [
                    _bullets(
                        f"Подготовка {trial}: детали",
                        f"Запуск {trial}: детали",
                        f"Итог {trial}: детали",
                    )
                ],
            ),
        ]
        for i in range(2, 2 + trial + 2):
            slides.append(
                _slide(
                    i,
                    LayoutType.CONTENT_1COL,
                    [_bullets(f"Пункт {i} А", f"Пункт {i} Б")],
                )
            )
        trials.append(
            _ir(_pad_with_neutral_slides(slides, target=max(10, len(slides))))
        )
    return trials


@pytest.mark.parametrize("ir", _trial_irs())
def test_apply_visual_policy_never_invents_words_beyond_the_fixed_comparison_labels(ir):
    before = _prose_words(ir)
    after = _prose_words(apply_visual_policy(ir))

    assert after - before <= {"до", "после"}


@pytest.mark.xfail(
    strict=True,
    reason="BUG: visual_policy._comparison_from_bullets hardcodes "
    "left_title/right_title as До/После (visual_policy.py:23-25) instead of "
    "deriving them from the slide content, so a Risks/Opportunities or "
    "Option-A/Option-B comparison is mislabeled as Before/After; see "
    "ARCHITECTURE.md §12.9.",
)
def test_comparison_titles_should_come_from_content_not_a_hardcoded_before_after_frame():
    slide = _slide(
        0,
        LayoutType.COMPARISON,
        [
            _bullets("Высокая стоимость внедрения"),
            _bullets("Быстрая окупаемость инвестиций"),
        ],
    )
    ir = _ir(_pad_with_neutral_slides([slide]))

    result = apply_visual_policy(ir)

    converted = result.slides[0].components[0]
    assert converted.left_title != "До"
    assert converted.right_title != "После"
