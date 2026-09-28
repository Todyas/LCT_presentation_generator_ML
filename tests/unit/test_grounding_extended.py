"""Extended coverage for app/core/agents/grounding.py.

Existing tests/unit/test_grounding.py keeps the original smoke tests; this
file adds the boundary and nested-model cases.
"""

from __future__ import annotations

import itertools

import pytest

from app.core.agents.grounding import near_duplicate_pairs, ungrounded_numbers
from app.models.presentation_ir import (
    ChartData,
    ChartSeries,
    MetricCard,
    ProcessData,
    ProcessStep,
    TableData,
)

# --------------------------------------------------------------------------- #
# Number normalization table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("output_text", "brief", "expected_ungrounded"),
    [
        # Comma and dot decimals must be treated as the same number.
        ("Рост составит 12.4 пункта", "Прогноз роста — 12,4 пункта", set()),
        ("Рост составит 12,4 пункта", "Прогноз роста — 12.4 пункта", set()),
        # Spaces before a percent sign must not create a distinct token.
        ("Конверсия выросла на 18 %", "Конверсия выросла на 18%", set()),
        ("Конверсия выросла на 18%", "Конверсия выросла на 18 %", set()),
        # Single digits without a percent sign are exempt regardless of brief.
        ("Проект пройдёт в 3 этапа", "Короткий бриф без чисел", set()),
        # Any percentage is never exempt, even single-digit ones.
        ("Рост всего на 5%", "Короткий бриф без чисел", {"5%"}),
        # Values >= 10 must be grounded, including bare years.
        ("План рассчитан на 2026 год", "Короткий бриф без чисел", {"2026"}),
        ("План рассчитан на 2026 год", "Бюджет утверждён на 2026 год", set()),
        # A genuinely invented large number is caught.
        ("Выручка достигнет 500 млн", "В брифе указана выручка 120 млн", {"500"}),
    ],
)
def test_number_normalization_table(output_text, brief, expected_ungrounded):
    assert ungrounded_numbers(output_text, brief) == expected_ungrounded


def test_slide_index_and_placeholder_idx_keys_are_always_ignored():
    value = {
        "slide_index": 12,
        "components": [{"placeholder_idx": 10, "text": "Следующий шаг без чисел"}],
    }

    assert ungrounded_numbers(value, "Короткий бриф без чисел") == set()


# --------------------------------------------------------------------------- #
# Nested models
# --------------------------------------------------------------------------- #


def test_ungrounded_number_hidden_in_chart_series_values_is_caught():
    chart = ChartData(
        chart_type="column",
        categories=["Q1", "Q2"],
        series=[ChartSeries(name="Выручка", values=[999, 20])],
    )

    # pydantic coerces the ints to floats, so the claim token is "999.0".
    assert "999.0" in ungrounded_numbers(chart, "Бриф без нужных чисел")


def test_ungrounded_number_hidden_in_table_rows_is_caught():
    table = TableData(headers=["Метрика"], rows=[["777"]])

    assert "777" in ungrounded_numbers(table, "Бриф без нужных чисел")


def test_ungrounded_number_hidden_in_metric_card_value_is_caught():
    card = MetricCard(label="Выручка", value="345")

    assert "345" in ungrounded_numbers(card, "Бриф без нужного числа")


def test_ungrounded_number_hidden_in_process_step_description_is_caught():
    process = ProcessData(
        steps=[
            ProcessStep(title="Шаг 1", description="Обработка 456 заявок"),
            ProcessStep(title="Шаг 2", description="Без чисел"),
            ProcessStep(title="Шаг 3", description="Без чисел"),
        ]
    )

    assert "456" in ungrounded_numbers(process, "Бриф без нужных чисел")


# --------------------------------------------------------------------------- #
# Known-bug probes
# --------------------------------------------------------------------------- #


def test_probe_signed_percentage_delta_is_currently_flagged_ungrounded():
    # The regex keeps a leading "+"/"-" as part of the matched token
    # (grounding.py:8 `[-+]?`), so a signed delta that restates a brief
    # number verbatim except for its sign is reported as invented.
    assert ungrounded_numbers("Рост +18%", "Рост на 18% по плану") == {"+18%"}


@pytest.mark.xfail(
    strict=True,
    reason="BUG: signed deltas like '+18%' are flagged ungrounded even when "
    "the brief states the same magnitude as '18%'; see grounding.py:8 "
    "(_NUMBER_RE keeps the sign in the token) and ARCHITECTURE.md §7.2.",
)
def test_signed_percentage_delta_should_be_grounded():
    assert ungrounded_numbers("Рост +18%", "Рост на 18% по плану") == set()


def test_unicode_minus_sign_is_not_captured_by_the_ascii_sign_class():
    # A Unicode minus (U+2212) is not part of the `[-+]` character class, so
    # the regex simply does not consume it and matches the bare "7%" — this
    # one is not a bug, just documenting the behaviour.
    assert ungrounded_numbers("Падение на −7%", "Падение на 7% ожидается") == set()


def test_thousand_separator_repeated_verbatim_grounds_correctly():
    assert ungrounded_numbers("Всего 1 340 клиентов", "Всего 1 340 клиентов") == set()


def test_probe_thousand_separator_reformatted_without_space_is_flagged():
    # The brief writes "1 340" (a thousands-separator space); the regex has
    # no notion of grouped digits, so it tokenizes the brief as "1" (exempt)
    # + "340" (claim). When the model reformats the same value as "1340"
    # (a common LLM normalization), that token is absent from the allowed
    # set and gets flagged as an invented number even though it is the same
    # figure from the brief.
    assert ungrounded_numbers("Всего 1340 клиентов", "Всего 1 340 клиентов") == {"1340"}


@pytest.mark.xfail(
    strict=True,
    reason="BUG: reformatting a thousands-separated brief number without "
    "the separator space ('1 340' -> '1340') is flagged as invented; see "
    "grounding.py:8 (_NUMBER_RE has no grouped-digit support).",
)
def test_thousand_separator_reformatted_without_space_should_be_grounded():
    assert ungrounded_numbers("Всего 1340 клиентов", "Всего 1 340 клиентов") == set()


# --------------------------------------------------------------------------- #
# near_duplicate_pairs
# --------------------------------------------------------------------------- #


def test_near_duplicate_pairs_ignores_distinct_messages():
    messages = [
        "Автоматизация сокращает время подготовки презентации",
        "Команда проверяет результат перед итоговым экспортом файла",
    ]

    assert near_duplicate_pairs(messages) == []


def test_near_duplicate_pairs_skips_empty_and_stopword_only_messages():
    messages = [
        "",
        "и в на с от",  # every token below the 3-letter minimum length is dropped
        "Автоматизация сокращает время подготовки презентации",
    ]

    assert near_duplicate_pairs(messages) == []


def test_near_duplicate_pairs_boundary_sits_exactly_at_default_threshold():
    pool = [
        "".join(letters)
        for letters in itertools.islice(
            itertools.product("abcdefghijklmnopqrstuvwxyz", repeat=3), 50
        )
    ]
    shared, a_only, b_only = pool[:39], pool[39:44], pool[44:50]
    message_a = " ".join(shared + a_only)
    message_b = " ".join(shared + b_only)

    # similarity is constructed to equal exactly 0.78 (39 shared / 50 union)
    assert near_duplicate_pairs([message_a, message_b], threshold=0.78) == [(0, 1)]
    assert near_duplicate_pairs([message_a, message_b], threshold=0.78 + 1e-9) == []
