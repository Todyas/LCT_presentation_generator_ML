from __future__ import annotations

import itertools

from app.core.agents.slot_filler import build_fallback_slide
from app.models.presentation_ir import PresentationIR
from app.models.template_manifest import (
    LayoutManifest,
    LayoutType,
    PlaceholderType,
    TemplateManifest,
)
from app.pipeline.layout_assignment import assign_layouts, pick_alternative_layout
from tests.factories import make_ir, make_manifest, make_outline_item, make_slot


def _layout(
    index: int,
    name: str,
    layout_type: LayoutType,
    *,
    body: tuple[float, float, float, float] | None = (0.07, 0.25, 0.86, 0.65),
) -> LayoutManifest:
    slots = [
        make_slot(
            placeholder_idx=0,
            placeholder_type=PlaceholderType.TITLE,
            x=0.07,
            y=0.05,
            w=0.86,
            h=0.15,
        )
    ]
    if body is not None:
        x, y, w, h = body
        slots.append(
            make_slot(
                placeholder_idx=1,
                placeholder_type=PlaceholderType.BODY,
                x=x,
                y=y,
                w=w,
                h=h,
            )
        )
    return LayoutManifest(
        layout_index=index, layout_name=name, layout_type=layout_type, slots=slots
    )


def _manifest(*, with_thanks: bool = True) -> TemplateManifest:
    layouts = [
        _layout(0, "Титульный слайд", LayoutType.TITLE_SLIDE),
        _layout(1, "Контент", LayoutType.CONTENT_1COL),
        _layout(2, "Контент с колонкой", LayoutType.CONTENT_1COL, body=(0.07, 0.25, 0.55, 0.6)),
        _layout(3, "Две колонки", LayoutType.CONTENT_2COL, body=(0.07, 0.25, 0.86, 0.6)),
        _layout(4, "Узкий текст", LayoutType.CONTENT_1COL, body=(0.3, 0.3, 0.6, 0.5)),
    ]
    if with_thanks:
        layouts.append(_layout(5, "Спасибо", LayoutType.SECTION_HEADER, body=None))
    return make_manifest(layouts=layouts)


def _ir(n: int = 12) -> PresentationIR:
    return make_ir(n)


def test_no_adjacent_slides_share_a_layout_and_layouts_are_varied():
    result = assign_layouts(_ir(12), _manifest())

    indices = [s.layout_index for s in result.slides]
    assert all(i is not None for i in indices)
    assert all(a != b for a, b in itertools.pairwise(indices))
    assert len({i for i in indices[1:]}) >= 3


def test_cover_gets_the_title_slide_layout():
    result = assign_layouts(_ir(12), _manifest())

    assert result.slides[0].layout_index == 0


def test_title_slide_layout_is_not_used_after_the_cover():
    result = assign_layouts(_ir(12), _manifest())

    assert 0 not in [s.layout_index for s in result.slides[1:]]


def test_closing_layouts_only_appear_on_the_last_slide():
    result = assign_layouts(_ir(12), _manifest())

    indices = [s.layout_index for s in result.slides]
    assert 5 not in indices[:-1]


def test_closing_layout_never_used_when_slide_is_not_last_even_if_alone():
    manifest = _manifest()
    result = assign_layouts(_ir(10), manifest)

    assert all(s.layout_index != 5 for s in result.slides[1:-1])


def test_assignment_is_deterministic():
    manifest = _manifest()
    ir = _ir(12)

    first = [s.layout_index for s in assign_layouts(ir, manifest).slides]
    second = [s.layout_index for s in assign_layouts(ir, manifest).slides]

    assert first == second


def test_single_viable_candidate_is_reused():
    manifest = make_manifest(
        layouts=[
            _layout(0, "Титульный слайд", LayoutType.TITLE_SLIDE),
            _layout(1, "Контент", LayoutType.CONTENT_1COL),
        ]
    )

    result = assign_layouts(_ir(10), manifest)

    assert [s.layout_index for s in result.slides[1:]] == [1] * 9


def test_alternative_layout_differs_from_current_and_from_neighbours():
    manifest = _manifest()
    ir = assign_layouts(_ir(12), manifest)
    position = 4

    alternative = pick_alternative_layout(ir, manifest, position)

    assert alternative is not None
    assert alternative != ir.slides[position].layout_index
    assert alternative != ir.slides[position - 1].layout_index


def test_fallback_slide_does_not_render_content_hint():
    hint = "Внутренняя заметка планировщика которую нельзя показывать"
    item = make_outline_item(
        3,
        key_message="Рост выручки ускорился. Основной вклад дал онлайн-канал.",
        content_hint=hint,
    )

    slide = build_fallback_slide(item, _manifest())

    rendered = [slide.title.text]
    for component in slide.components:
        rendered.extend(b.text for b in component.items)
    assert all(hint not in text for text in rendered)
    assert all("планировщика" not in text for text in rendered)


def test_fallback_slide_bullets_are_short_and_never_equal_the_title():
    item = make_outline_item(
        3,
        key_message="Рост выручки ускорился. Основной вклад дал онлайн-канал.",
        content_hint="служебная подсказка",
    )

    slide = build_fallback_slide(item, _manifest())

    bullets = [b.text for c in slide.components for b in c.items]
    assert len(bullets) <= 2
    assert all(len(b.split()) <= 15 for b in bullets)
    assert slide.title.text.casefold() not in {b.casefold() for b in bullets}
