from app.core.agents.grounding import near_duplicate_pairs, ungrounded_numbers


def test_grounding_allows_numbers_present_in_brief():
    value = {"title": "Рост на 30%", "components": [{"text": "Выручка — 120 млн"}]}

    assert (
        ungrounded_numbers(value, "Ожидается рост на 30%, выручка составит 120 млн")
        == set()
    )


def test_grounding_rejects_invented_business_number():
    value = {"title": "Рост на 50%"}

    assert ungrounded_numbers(value, "В брифе указан рост на 30%") == {"50%"}


def test_grounding_ignores_structural_indices():
    value = {"slide_index": 12, "placeholder_idx": 10, "title": "Следующий шаг"}

    assert ungrounded_numbers(value, "Короткий бриф без чисел") == set()


def test_near_duplicate_messages_are_detected():
    messages = [
        "Автоматизация сокращает время подготовки презентации",
        "Автоматизация сокращает время на подготовку презентации",
        "Команда проверяет результат перед экспортом",
    ]

    assert near_duplicate_pairs(messages)
