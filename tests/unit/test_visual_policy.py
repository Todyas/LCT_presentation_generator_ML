from app.core.agents.visual_policy import apply_visual_policy
from app.core.auditor.visual_audit import run_visual_variety_audit
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    IconListData,
    PresentationIR,
    SlideIR,
    TitleComponent,
)
from app.models.template_manifest import LayoutType


def _slide(index: int) -> SlideIR:
    return SlideIR(
        slide_index=index,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text=f"Вывод {index}"),
        components=[
            BulletBlock(
                items=[
                    BulletItem(text="Скорость: меньше ручных действий"),
                    BulletItem(text="Контроль: единые правила"),
                ]
            )
        ],
    )


def test_visual_policy_breaks_three_slide_text_streak_without_new_facts():
    ir = PresentationIR(
        variant="A", template_source_hash="x", slides=[_slide(i) for i in range(10)]
    )

    diversified = apply_visual_policy(ir)

    assert isinstance(diversified.slides[2].components[0], IconListData)
    issues = run_visual_variety_audit(diversified)
    assert not any("consecutive" in issue.message for issue in issues)


# A valid bullet (<=15 words) that is longer than IconListItem.title (60 chars)
# and ProcessStep.title (50 chars) and has no "label: description" separator.
_LONG_BULLET = "Планируется расширение продуктовой линейки и снижение операционных издержек компании"


def _long_bullet_slide(index: int, layout_type: LayoutType) -> SlideIR:
    return SlideIR(
        slide_index=index,
        layout_type=layout_type,
        title=TitleComponent(text=f"Вывод {index}"),
        components=[BulletBlock(items=[BulletItem(text=_LONG_BULLET) for _ in range(3)])],
    )


def test_visual_policy_keeps_bullets_that_do_not_fit_an_icon_list():
    slides = [_long_bullet_slide(i, LayoutType.CONTENT_1COL) for i in range(10)]
    ir = PresentationIR(variant="B", template_source_hash="x", slides=slides)

    diversified = apply_visual_policy(ir)

    assert diversified.slides[2].components == slides[2].components


def test_visual_policy_keeps_bullets_that_do_not_fit_process_steps():
    slides = [_long_bullet_slide(i, LayoutType.PROCESS_TIMELINE) for i in range(10)]
    ir = PresentationIR(variant="C", template_source_hash="x", slides=slides)

    diversified = apply_visual_policy(ir)

    assert diversified.slides[0].components == slides[0].components
