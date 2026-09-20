from __future__ import annotations

from app.models.template_manifest import LayoutSlot, LayoutType, PlaceholderType

_NAME_RULES: list[tuple[tuple[str, ...], LayoutType]] = [
    (("title", "титул"), LayoutType.TITLE_SLIDE),
    (("section", "раздел"), LayoutType.SECTION_HEADER),
    (("table", "таблиц"), LayoutType.TABLE_FOCUSED),
    (("chart", "график", "диаграмм"), LayoutType.CHART_FOCUSED),
    (("comparison", "сравнен"), LayoutType.COMPARISON),
    (("two content", "2 колон", "two colum"), LayoutType.CONTENT_2COL),
    (("blank", "пуст"), LayoutType.BLANK),
]


def _slots_disjoint(a: LayoutSlot, b: LayoutSlot) -> bool:
    a_right = a.normalized.x + a.normalized.w
    b_right = b.normalized.x + b.normalized.w
    return a_right <= b.normalized.x or b_right <= a.normalized.x


def classify_layout(layout_name: str, slots: list[LayoutSlot]) -> LayoutType:
    name_lower = layout_name.lower()
    for needles, layout_type in _NAME_RULES:
        if any(n in name_lower for n in needles):
            return layout_type

    if not slots:
        return LayoutType.BLANK

    types = [s.placeholder_type for s in slots]
    if set(types) == {PlaceholderType.TITLE, PlaceholderType.SUBTITLE}:
        return LayoutType.TITLE_SLIDE

    if PlaceholderType.TABLE in types:
        return LayoutType.TABLE_FOCUSED

    if PlaceholderType.CHART in types:
        return LayoutType.CHART_FOCUSED

    body_slots = [s for s in slots if s.placeholder_type == PlaceholderType.BODY]
    if len(body_slots) >= 2:
        for i, a in enumerate(body_slots):
            for b in body_slots[i + 1:]:
                if _slots_disjoint(a, b):
                    return LayoutType.CONTENT_2COL

    if len(body_slots) == 1:
        return LayoutType.CONTENT_1COL

    return LayoutType.UNKNOWN
