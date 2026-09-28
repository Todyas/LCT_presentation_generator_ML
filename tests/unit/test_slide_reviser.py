"""Coverage for app/core/agents/slide_reviser.py (previously untested)."""

from __future__ import annotations

import pytest

from app.api.schemas import SlideRevisionRequest
from app.core.agents.prompt_registry import PromptRegistry
from app.core.agents.slide_reviser import SlideRevisionError, revise_slide
from app.models.presentation_ir import BulletBlock, BulletItem, SlideIR, TitleComponent
from app.models.template_manifest import LayoutType
from tests.factories import symmetric_manifest

REGISTRY = PromptRegistry("skills")
NO_NUMBER_BRIEF = "Общий бриф без чисел для проверки правки слайда."


def _slide(slide_index: int = 4, text: str = "Первый пункт") -> SlideIR:
    return SlideIR(
        slide_index=slide_index,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text="Исходный вывод"),
        components=[
            BulletBlock(items=[BulletItem(text=text), BulletItem(text="Второй пункт")])
        ],
    )


async def _revise(
    llm, *, current_slide=None, instructions="Улучши слайд", max_retries=2
):
    return await revise_slide(
        current_slide=current_slide or _slide(),
        brief=NO_NUMBER_BRIEF,
        instructions=instructions,
        manifest=symmetric_manifest(),
        llm=llm,
        registry=REGISTRY,
        model="qwen",
        max_retries=max_retries,
    )


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_revised_slide_keeps_original_slide_index_even_if_llm_changes_it(
    scripted_llm,
):
    current = _slide(slide_index=4)
    llm_returned = SlideIR(
        slide_index=999,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text="Новый вывод"),
        components=[BulletBlock(items=[BulletItem(text="Обновлённый пункт")])],
    )
    scripted_llm.script(SlideIR, llm_returned)

    result = await _revise(scripted_llm, current_slide=current)

    assert result.slide_index == 4
    assert result.title.text == "Новый вывод"


async def test_prompt_contains_current_slide_json_instructions_and_available_layouts(
    scripted_llm,
):
    current = _slide(slide_index=4, text="Уникальный маркерный текст")
    scripted_llm.script(SlideIR, current)

    await _revise(
        scripted_llm, current_slide=current, instructions="Сократи текст слайда"
    )

    prompt = scripted_llm.calls[-1].user_prompt
    assert "Уникальный маркерный текст" in prompt
    assert "Сократи текст слайда" in prompt
    manifest_types = {
        layout.layout_type.value for layout in symmetric_manifest().layouts
    }
    semantic_types = {
        layout_type.value
        for layout_type in LayoutType
        if layout_type not in {LayoutType.UNKNOWN, LayoutType.BLANK}
    }
    expected_line = ", ".join(sorted(manifest_types | semantic_types))
    assert expected_line in prompt
    assert "UNKNOWN" not in expected_line


# --------------------------------------------------------------------------- #
# Invented numbers
# --------------------------------------------------------------------------- #


async def test_invented_number_retries_then_exhausts_to_slide_revision_error(
    scripted_llm,
):
    bad = SlideIR(
        slide_index=4,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text="Рост на 777%"),
        components=[BulletBlock(items=[BulletItem(text="Пункт")])],
    )
    scripted_llm.script(SlideIR, bad)  # repeats forever -> always invented

    with pytest.raises(SlideRevisionError):
        await _revise(scripted_llm, max_retries=1)

    assert len(scripted_llm.calls) == 2  # max_retries=1 -> 2 attempts total


async def test_invented_number_then_grounded_response_is_accepted(scripted_llm):
    bad = SlideIR(
        slide_index=4,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text="Рост на 777%"),
        components=[BulletBlock(items=[BulletItem(text="Пункт")])],
    )
    good = _slide(slide_index=4, text="Пункт без чисел")
    scripted_llm.script(SlideIR, bad, good)

    result = await _revise(scripted_llm)

    assert len(scripted_llm.calls) == 2
    assert "PREVIOUS ATTEMPT FAILED VALIDATION" in scripted_llm.calls[1].user_prompt
    assert result.slide_index == 4


# --------------------------------------------------------------------------- #
# Visual request must not come back as bullet-only text
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "trigger_word",
    [
        "график",
        "добавь схему",
        "нужен визуальный акцент",
        "смени layout",
        "другой макет",
    ],
)
async def test_visual_instruction_with_bullet_only_response_retries(
    scripted_llm, trigger_word
):
    bullet_only = _slide(slide_index=4, text="Просто текст без визуала")
    visual_response = SlideIR(
        slide_index=4,
        layout_type=LayoutType.PROCESS_TIMELINE,
        title=TitleComponent(text="Вывод с визуалом"),
        components=[
            {
                "type": "process",
                "steps": [
                    {"title": "Шаг 1"},
                    {"title": "Шаг 2"},
                    {"title": "Шаг 3"},
                ],
            }
        ],
    )
    scripted_llm.script(SlideIR, bullet_only, visual_response)

    result = await _revise(scripted_llm, instructions=f"Пожалуйста, {trigger_word}")

    assert len(scripted_llm.calls) == 2
    assert "PREVIOUS ATTEMPT FAILED VALIDATION" in scripted_llm.calls[1].user_prompt
    assert result.components[0].type == "process"


async def test_bullet_only_response_is_accepted_when_no_visual_was_requested(
    scripted_llm,
):
    bullet_only = _slide(slide_index=4, text="Просто текст без визуала")
    scripted_llm.script(SlideIR, bullet_only)

    result = await _revise(scripted_llm, instructions="Сократи текст, сохранив факты.")

    assert len(scripted_llm.calls) == 1
    assert isinstance(result.components[0], BulletBlock)


# --------------------------------------------------------------------------- #
# SlideRevisionRequest.as_instructions()
# --------------------------------------------------------------------------- #


def test_as_instructions_empty_request_yields_default_instruction():
    assert (
        SlideRevisionRequest().as_instructions()
        == "Улучши слайд, сохранив его смысл и факты."
    )


@pytest.mark.parametrize(
    ("flag", "expected_fragment"),
    [
        ("shorten_text", "Сократи текст"),
        ("make_action_title", "Сделай заголовок выводом"),
        ("change_layout", "Подбери другой подходящий макет"),
        ("add_visual", "Добавь уместный визуальный компонент"),
        ("regenerate", "Пересобери слайд"),
    ],
)
def test_as_instructions_each_flag_adds_its_own_instruction(flag, expected_fragment):
    request = SlideRevisionRequest(**{flag: True})

    assert expected_fragment in request.as_instructions()


def test_as_instructions_combines_all_flags_and_appends_stripped_comment():
    request = SlideRevisionRequest(
        shorten_text=True,
        make_action_title=True,
        change_layout=True,
        add_visual=True,
        regenerate=True,
        comment="  Добавь акцент на клиентов.  ",
    )

    instructions = request.as_instructions()

    for fragment in (
        "Сократи текст",
        "Сделай заголовок выводом",
        "Подбери другой подходящий макет",
        "Добавь уместный визуальный компонент",
        "Пересобери слайд",
    ):
        assert fragment in instructions
    assert instructions.endswith("Добавь акцент на клиентов.")
    assert "  Добавь акцент на клиентов.  " not in instructions


def test_as_instructions_blank_comment_is_ignored():
    request = SlideRevisionRequest(comment="   ")

    assert request.as_instructions() == "Улучши слайд, сохранив его смысл и факты."
