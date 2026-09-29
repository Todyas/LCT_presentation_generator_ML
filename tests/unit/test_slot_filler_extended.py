"""Extended coverage for app/core/agents/slot_filler.py.

Existing tests/unit/test_slot_filler.py keeps the original smoke tests;
this file drives every retry rule and the fallback-slide builder.
"""

from __future__ import annotations

import pytest

from app.core.agents.prompt_registry import PromptRegistry
from app.core.agents.slot_filler import (
    _ALLOWED_COMPONENTS_BY_LAYOUT,
    SlotFillError,
    build_fallback_slide,
    fill_slide,
)
from app.models.outline import OutlineItem
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    ChartData,
    ChartSeries,
    ComparisonData,
    MetricCard,
    ProcessData,
    ProcessStep,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import LayoutType
from tests.factories import make_outline_item, poor_manifest, symmetric_manifest

REGISTRY = PromptRegistry("skills")
NO_NUMBER_BRIEF = "Общий бриф без чисел для проверки заполнения слайдов."


def _slide(
    layout_type: LayoutType,
    components,
    *,
    slide_index: int = 3,
    title: str = "Заголовок-вывод",
) -> SlideIR:
    return SlideIR(
        slide_index=slide_index,
        layout_type=layout_type,
        title=TitleComponent(text=title),
        components=components,
    )


def _good_components(layout_type: LayoutType) -> list:
    """Digit-free content that always satisfies `_slide_quality_problems`."""
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
    if layout_type == LayoutType.CONTENT_1COL:
        return [
            BulletBlock(
                items=[BulletItem(text="Первый пункт"), BulletItem(text="Второй пункт")]
            )
        ]
    return [BulletBlock(items=[BulletItem(text="Общий пункт")])]


# --------------------------------------------------------------------------- #
# Allowed component types depend on the REQUESTED layout
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("requested_layout", list(_ALLOWED_COMPONENTS_BY_LAYOUT))
async def test_prompt_lists_allowed_components_for_requested_layout_not_resolved(
    scripted_llm, requested_layout
):
    # poor_manifest() only has TITLE_SLIDE + UNKNOWN, so every non-title
    # request resolves to the UNKNOWN layout — proving the prompt's allowed
    # component list still keys off the *requested* semantic layout.
    manifest = poor_manifest()
    item = make_outline_item(3, layout_type=requested_layout)
    scripted_llm.script(
        SlideIR,
        _slide(requested_layout, _good_components(requested_layout), slide_index=3),
    )

    await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief=NO_NUMBER_BRIEF,
        llm=scripted_llm,
        registry=REGISTRY,
        model="qwen",
    )

    prompt = scripted_llm.calls[-1].user_prompt
    expected = _ALLOWED_COMPONENTS_BY_LAYOUT[requested_layout]
    assert ", ".join(expected) in prompt
    resolved = manifest.find_layout_or_fallback(requested_layout, 3)
    assert (
        resolved.layout_type != requested_layout
    )  # sanity: manifest really did resolve elsewhere


async def test_default_allowed_components_for_layout_without_explicit_rule(
    scripted_llm,
):
    manifest = symmetric_manifest()
    item = make_outline_item(0, layout_type=LayoutType.QUOTE)
    scripted_llm.script(
        SlideIR,
        _slide(LayoutType.QUOTE, _good_components(LayoutType.QUOTE), slide_index=0),
    )

    await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief=NO_NUMBER_BRIEF,
        llm=scripted_llm,
        registry=REGISTRY,
        model="qwen",
    )

    assert "bullet_block" in scripted_llm.calls[-1].user_prompt


# --------------------------------------------------------------------------- #
# Every _slide_quality_problems rule retries then succeeds
# --------------------------------------------------------------------------- #

_QUALITY_CASES = [
    pytest.param(
        LayoutType.KPI_DASHBOARD,
        [MetricCard(label="A", value="1"), MetricCard(label="B", value="2")],
        "at least three metric_card",
        id="kpi_needs_three_cards",
    ),
    pytest.param(
        LayoutType.CHART_FOCUSED,
        [BulletBlock(items=[BulletItem(text="Нет графика")])],
        "CHART_FOCUSED requires a chart",
        id="chart_focused_needs_chart",
    ),
    pytest.param(
        LayoutType.TABLE_FOCUSED,
        [BulletBlock(items=[BulletItem(text="Нет таблицы")])],
        "TABLE_FOCUSED requires a table",
        id="table_focused_needs_table",
    ),
    pytest.param(
        LayoutType.CONTENT_2COL,
        [BulletBlock(items=[BulletItem(text="Один блок без визуала")])],
        "CONTENT_2COL requires two bullet blocks",
        id="content_2col_needs_two_blocks_or_visual",
    ),
    pytest.param(
        LayoutType.COMPARISON,
        [BulletBlock(items=[BulletItem(text="Один текстовый блок")])],
        "COMPARISON requires a comparison component",
        id="comparison_needs_comparison_or_two_blocks",
    ),
    pytest.param(
        LayoutType.PROCESS_TIMELINE,
        [BulletBlock(items=[BulletItem(text="Нет процесса")])],
        "PROCESS_TIMELINE requires a process",
        id="process_timeline_needs_process",
    ),
    pytest.param(
        LayoutType.CONTENT_1COL,
        [BulletBlock(items=[BulletItem(text="Только один пункт")])],
        "at least two distinct supporting points",
        id="content_1col_needs_two_points",
    ),
    pytest.param(
        LayoutType.CONTENT_1COL,
        [
            BulletBlock(
                items=[
                    BulletItem(text="Тезис: общая мысль"),
                    BulletItem(text="Второй пункт"),
                ]
            )
        ],
        "generic 'Тезис:' labels are forbidden",
        id="tezis_label_forbidden",
    ),
]


@pytest.mark.parametrize(
    ("layout_type", "bad_components", "expected_problem_text"), _QUALITY_CASES
)
async def test_quality_problem_triggers_retry_then_succeeds(
    scripted_llm, layout_type, bad_components, expected_problem_text
):
    manifest = symmetric_manifest()
    item = make_outline_item(3, layout_type=layout_type)
    bad = _slide(layout_type, bad_components, slide_index=3)
    good = _slide(layout_type, _good_components(layout_type), slide_index=3)
    scripted_llm.script(SlideIR, bad, good)

    result = await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief=NO_NUMBER_BRIEF,
        llm=scripted_llm,
        registry=REGISTRY,
        model="qwen",
    )

    assert isinstance(result, SlideIR)
    assert len(scripted_llm.calls) == 2
    second_prompt = scripted_llm.calls[1].user_prompt
    assert "PREVIOUS ATTEMPT FAILED VALIDATION" in second_prompt
    assert expected_problem_text in second_prompt


# --------------------------------------------------------------------------- #
# Invented numbers
# --------------------------------------------------------------------------- #


async def test_invented_number_in_nested_metric_card_exhausts_retries(scripted_llm):
    manifest = symmetric_manifest()
    item = make_outline_item(3, layout_type=LayoutType.KPI_DASHBOARD)
    bad = _slide(
        LayoutType.KPI_DASHBOARD,
        [
            MetricCard(label="Выручка", value="500"),
            MetricCard(label="Конверсия", value="Высокая"),
            MetricCard(label="NPS", value="Стабилен"),
        ],
        slide_index=3,
    )
    scripted_llm.script(SlideIR, bad)  # repeats forever -> always invented

    with pytest.raises(SlotFillError) as excinfo:
        await fill_slide(
            item=item,
            variant="A",
            manifest=manifest,
            brief=NO_NUMBER_BRIEF,
            llm=scripted_llm,
            registry=REGISTRY,
            model="qwen",
            max_retries=1,
        )

    assert excinfo.value.slide_index == 3
    assert len(scripted_llm.calls) == 2  # max_retries=1 -> 2 attempts total


# --------------------------------------------------------------------------- #
# slide_index / layout_type are always forced to the item's values
# --------------------------------------------------------------------------- #


async def test_result_always_uses_item_slide_index_and_resolved_layout_type(
    scripted_llm,
):
    manifest = symmetric_manifest()  # has no PROCESS_TIMELINE layout
    item = make_outline_item(7, layout_type=LayoutType.PROCESS_TIMELINE)
    llm_returned = _slide(
        LayoutType.PROCESS_TIMELINE,
        _good_components(LayoutType.PROCESS_TIMELINE),
        slide_index=999,  # deliberately wrong
    )
    scripted_llm.script(SlideIR, llm_returned)

    result = await fill_slide(
        item=item,
        variant="A",
        manifest=manifest,
        brief=NO_NUMBER_BRIEF,
        llm=scripted_llm,
        registry=REGISTRY,
        model="qwen",
    )

    resolved = manifest.find_layout_or_fallback(LayoutType.PROCESS_TIMELINE, 7)
    assert result.slide_index == 7
    assert result.layout_type == resolved.layout_type
    assert resolved.layout_type != LayoutType.PROCESS_TIMELINE  # sanity


# --------------------------------------------------------------------------- #
# build_fallback_slide
# --------------------------------------------------------------------------- #


def test_fallback_slide_title_is_truncated_with_ellipsis_when_key_message_is_long():
    long_message = " ".join(
        f"слово{i}" for i in range(20)
    )  # over 120, under the 300 cap
    item = OutlineItem(
        slide_index=1,
        working_title="Заголовок",
        key_message=long_message,
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint="Отдельная конкретная деталь источника",
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    assert len(slide.title.text) <= 120
    assert slide.title.text.endswith("…")


def test_fallback_slide_title_is_kept_verbatim_when_short_enough():
    item = OutlineItem(
        slide_index=1,
        working_title="Заголовок",
        key_message="Короткий вывод из пары слов",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint="Отдельная конкретная деталь источника",
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    assert slide.title.text == "Короткий вывод из пары слов"


def test_fallback_slide_bullets_never_exceed_fifteen_words():
    item = OutlineItem(
        slide_index=1,
        working_title="Заголовок",
        key_message="Короткий вывод",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint=" ".join(f"деталь{i}" for i in range(30)),
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    for bullet in slide.components[0].items:
        assert len(bullet.text.split()) <= 15


def test_fallback_slide_keeps_the_outline_items_slide_index():
    item = OutlineItem(
        slide_index=11,
        working_title="Заголовок",
        key_message="Некий содержательный вывод слайда",
        suggested_layout_type=LayoutType.CONTENT_2COL,
        content_hint="Конкретная деталь для этого слайда",
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    assert slide.slide_index == 11


def test_fallback_slide_never_duplicates_title_as_the_only_bullet():
    item = OutlineItem(
        slide_index=2,
        working_title="Заголовок",
        key_message="Короткое сообщение из пяти слов",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint="",  # falls back to key_message
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    bullets = slide.components[0].items
    assert not (
        len(bullets) == 1 and bullets[0].text.casefold() == slide.title.text.casefold()
    )


@pytest.mark.parametrize("layout_type", list(LayoutType))
@pytest.mark.parametrize("slide_index", [0, 5])
def test_fallback_slide_works_for_every_requested_layout_type(layout_type, slide_index):
    item = OutlineItem(
        slide_index=slide_index,
        working_title="Заголовок",
        key_message="Содержательный вывод для этого слайда",
        suggested_layout_type=layout_type,
        content_hint="Конкретная деталь источника для слайда",
    )

    slide = build_fallback_slide(item, symmetric_manifest())

    assert isinstance(slide, SlideIR)
    assert slide.slide_index == slide_index
    if layout_type == LayoutType.TITLE_SLIDE:
        if slide_index == 0:
            assert slide.layout_type == LayoutType.TITLE_SLIDE
        else:
            assert slide.layout_type != LayoutType.TITLE_SLIDE
