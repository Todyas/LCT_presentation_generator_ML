"""Extended coverage for app/core/agents/narrative_architect.py.

Existing tests/unit/test_narrative_architect.py keeps the original smoke
tests; this file drives the retry/rejection state machine with ScriptedLLM.
"""

from __future__ import annotations

import pytest

from app.core.agents.narrative_architect import (
    _SUPPORTED_SEMANTIC_LAYOUTS,
    VARIANT_DESCRIPTIONS,
    build_outline,
)
from app.core.agents.prompt_registry import PromptRegistry
from app.models.outline import Outline
from app.models.template_manifest import LayoutType
from tests.factories import (
    grounded_brief,
    make_outline,
    make_outline_item,
    poor_manifest,
    symmetric_manifest,
)

REGISTRY = PromptRegistry("skills")


def _good_outline(variant: str = "A", n: int = 10) -> Outline:
    return make_outline(n, variant=variant)


async def _run(llm, manifest=None, brief=None, variant="A"):
    return await build_outline(
        brief=brief or grounded_brief(),
        manifest=manifest or symmetric_manifest(),
        variant=variant,
        llm=llm,
        registry=REGISTRY,
        model="qwen",
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_happy_path_outline_accepted_after_exactly_one_call(scripted_llm):
    good = _good_outline()
    scripted_llm.script(Outline, good)

    result = await _run(scripted_llm)

    assert result == good
    assert scripted_llm.calls[-1].response_model is Outline
    assert len([c for c in scripted_llm.calls if c.response_model is Outline]) == 1


# --------------------------------------------------------------------------- #
# Rejection rules
# --------------------------------------------------------------------------- #


def _outline_missing_comparison() -> Outline:
    cycle = [
        LayoutType.TITLE_SLIDE,
        LayoutType.CONTENT_1COL,
        LayoutType.CONTENT_2COL,
        LayoutType.CONTENT_1COL,
        LayoutType.PROCESS_TIMELINE,
        LayoutType.CONTENT_1COL,
        LayoutType.KPI_DASHBOARD,
        LayoutType.CONTENT_2COL,
        LayoutType.CONTENT_1COL,
        LayoutType.CHART_FOCUSED,
    ]
    return make_outline(10, layout_types=cycle)


def _outline_missing_process_timeline() -> Outline:
    cycle = [
        LayoutType.TITLE_SLIDE,
        LayoutType.CONTENT_1COL,
        LayoutType.CONTENT_2COL,
        LayoutType.COMPARISON,
        LayoutType.CONTENT_1COL,
        LayoutType.KPI_DASHBOARD,
        LayoutType.CONTENT_1COL,
        LayoutType.CONTENT_2COL,
        LayoutType.CONTENT_1COL,
        LayoutType.CHART_FOCUSED,
    ]
    return make_outline(10, layout_types=cycle)


def _outline_three_content_1col_in_a_row() -> Outline:
    cycle = [
        LayoutType.TITLE_SLIDE,
        LayoutType.CONTENT_1COL,
        LayoutType.CONTENT_1COL,
        LayoutType.CONTENT_1COL,
        LayoutType.COMPARISON,
        LayoutType.PROCESS_TIMELINE,
        LayoutType.CONTENT_2COL,
        LayoutType.KPI_DASHBOARD,
        LayoutType.CONTENT_2COL,
        LayoutType.CHART_FOCUSED,
    ]
    return make_outline(10, layout_types=cycle)


def _outline_fewer_than_three_compositions() -> Outline:
    items = [
        make_outline_item(i, layout_type=LayoutType.CONTENT_1COL) for i in range(9)
    ]
    items.append(make_outline_item(9, layout_type=LayoutType.SECTION_HEADER))
    return Outline(variant="A", items=items)


def _outline_with_invented_number() -> Outline:
    good = _good_outline()
    items = list(good.items)
    items[2] = items[2].model_copy(
        update={"key_message": items[2].key_message + " — рост на 999% год к году"}
    )
    return good.model_copy(update={"items": items})


def _outline_with_near_duplicates() -> Outline:
    good = _good_outline()
    items = list(good.items)
    items[0] = items[0].model_copy(
        update={"key_message": "Автоматизация сокращает время подготовки презентации"}
    )
    items[4] = items[4].model_copy(
        update={
            "key_message": "Автоматизация сокращает время на подготовку презентации"
        }
    )
    return good.model_copy(update={"items": items})


_REJECTION_CASES = [
    pytest.param(
        _outline_with_invented_number, "numbers absent from BRIEF", id="invented_number"
    ),
    pytest.param(
        _outline_with_near_duplicates,
        "near-duplicate slide messages",
        id="near_duplicates",
    ),
    pytest.param(
        _outline_fewer_than_three_compositions,
        "fewer than three distinct slide compositions",
        id="fewer_than_three_compositions",
    ),
    pytest.param(
        _outline_missing_comparison,
        "missing a qualitative comparison slide",
        id="missing_comparison",
    ),
    pytest.param(
        _outline_missing_process_timeline,
        "missing a process or timeline slide",
        id="missing_process_timeline",
    ),
    pytest.param(
        _outline_three_content_1col_in_a_row,
        "three consecutive CONTENT_1COL slides",
        id="three_content_1col_in_a_row",
    ),
]


@pytest.mark.parametrize(
    ("bad_outline_factory", "expected_problem_text"), _REJECTION_CASES
)
async def test_each_rejection_rule_triggers_retry_with_feedback(
    scripted_llm, bad_outline_factory, expected_problem_text
):
    bad = bad_outline_factory()
    good = _good_outline()
    scripted_llm.script(Outline, bad, good)

    result = await _run(scripted_llm)

    assert result == good
    calls = [c for c in scripted_llm.calls if c.response_model is Outline]
    assert len(calls) == 2
    second_prompt = calls[1].user_prompt
    assert "PREVIOUS OUTLINE WAS REJECTED" in second_prompt
    assert expected_problem_text in second_prompt


# --------------------------------------------------------------------------- #
# Schema validation retry
# --------------------------------------------------------------------------- #


async def test_schema_validation_error_retries_with_field_location_feedback(
    scripted_llm,
):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as captured:
        Outline.model_validate(
            {
                "variant": "A",
                "items": [make_outline_item(i).model_dump() for i in range(9)],
            }
        )
    good = _good_outline()
    scripted_llm.script(Outline, captured.value, good)

    result = await _run(scripted_llm)

    assert result == good
    calls = [c for c in scripted_llm.calls if c.response_model is Outline]
    assert len(calls) == 2
    second_prompt = calls[1].user_prompt
    assert "FAILED JSON SCHEMA VALIDATION" in second_prompt
    assert "items" in second_prompt


# --------------------------------------------------------------------------- #
# Best-of-three fallback
# --------------------------------------------------------------------------- #


def _grounded_imperfect_outline_with_duplicate_pairs(
    pairs: list[tuple[int, int]],
) -> Outline:
    outline = _outline_missing_comparison()
    items = list(outline.items)
    phrases = [
        "Финансовый прогноз показывает устойчивый рост показателей",
        "Операционная эффективность повышается за счёт автоматизации процессов",
    ]
    for pair_index, (a, b) in enumerate(pairs):
        text = phrases[pair_index % len(phrases)]
        items[a] = items[a].model_copy(update={"key_message": text})
        items[b] = items[b].model_copy(update={"key_message": text})
    return outline.model_copy(update={"items": items})


async def test_returns_grounded_outline_with_fewest_duplicate_pairs_not_the_last_one(
    scripted_llm,
):
    attempt_two_duplicates = _grounded_imperfect_outline_with_duplicate_pairs(
        [(0, 1), (4, 5)]
    )
    attempt_zero_duplicates = _grounded_imperfect_outline_with_duplicate_pairs([])
    attempt_one_duplicate = _grounded_imperfect_outline_with_duplicate_pairs([(0, 1)])
    scripted_llm.script(
        Outline, attempt_two_duplicates, attempt_zero_duplicates, attempt_one_duplicate
    )

    result = await _run(scripted_llm)

    assert result == attempt_zero_duplicates
    calls = [c for c in scripted_llm.calls if c.response_model is Outline]
    assert len(calls) == 3


async def test_all_three_attempts_with_invented_numbers_raises_value_error(
    scripted_llm,
):
    def _invented(n: str) -> Outline:
        good = _good_outline()
        items = list(good.items)
        items[0] = items[0].model_copy(
            update={"key_message": items[0].key_message + f" — {n}% в этом квартале"}
        )
        return good.model_copy(update={"items": items})

    scripted_llm.script(Outline, _invented("911"), _invented("922"), _invented("933"))

    with pytest.raises(ValueError, match="outline grounding failed"):
        await _run(scripted_llm)

    calls = [c for c in scripted_llm.calls if c.response_model is Outline]
    assert len(calls) == 3


async def test_never_makes_a_fourth_llm_call(scripted_llm):
    bad = _outline_with_invented_number()
    scripted_llm.script(Outline, bad)  # single entry repeats forever -> always rejected

    with pytest.raises(ValueError):
        await _run(scripted_llm)

    calls = [c for c in scripted_llm.calls if c.response_model is Outline]
    assert len(calls) == 3


# --------------------------------------------------------------------------- #
# Prompt contents
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variant", ["A", "B", "C"])
async def test_prompt_contains_variant_description_and_slide_bounds(
    scripted_llm, variant
):
    good = _good_outline(variant=variant)
    scripted_llm.script(Outline, good)

    await _run(scripted_llm, variant=variant)

    prompt = scripted_llm.calls[-1].user_prompt
    assert VARIANT_DESCRIPTIONS[variant] in prompt
    assert "10" in prompt


async def test_available_layout_types_include_all_semantic_layouts_even_with_poor_manifest(
    scripted_llm,
):
    good = _good_outline()
    scripted_llm.script(Outline, good)

    await _run(scripted_llm, manifest=poor_manifest())

    prompt = scripted_llm.calls[-1].user_prompt
    for layout_type in _SUPPORTED_SEMANTIC_LAYOUTS:
        assert layout_type in prompt


async def test_uses_latest_narrative_architect_skill_version(scripted_llm):
    good = _good_outline()
    scripted_llm.script(Outline, good)

    await _run(scripted_llm)

    # A phrase unique to skills/narrative_architect/v3.yaml (absent from v1/v2).
    assert "ИСТОЧНИК ИСТИНЫ" in scripted_llm.calls[-1].system_prompt
