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
                items=[BulletItem(text="Скорость: меньше ручных действий"), BulletItem(text="Контроль: единые правила")]
            )
        ],
    )


def test_visual_policy_breaks_three_slide_text_streak_without_new_facts():
    ir = PresentationIR(variant="A", template_source_hash="x", slides=[_slide(i) for i in range(10)])

    diversified = apply_visual_policy(ir)

    assert isinstance(diversified.slides[2].components[0], IconListData)
    issues = run_visual_variety_audit(diversified)
    assert not any("consecutive" in issue.message for issue in issues)
