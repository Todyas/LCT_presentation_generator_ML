"""Coverage for app/core/auditor/visual_audit.py (previously untested)."""

from __future__ import annotations

from app.core.auditor.visual_audit import run_visual_variety_audit
from app.models.audit_report import IssueType, Severity
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    MetricCard,
    PresentationIR,
    SlideIR,
    TableData,
    TitleComponent,
)
from app.models.template_manifest import LayoutType


def _slide(index: int, layout_type: LayoutType, components: list) -> SlideIR:
    return SlideIR(
        slide_index=index,
        layout_type=layout_type,
        title=TitleComponent(text=f"Слайд {index}"),
        components=components,
    )


def _bullets(*texts: str) -> BulletBlock:
    return BulletBlock(items=[BulletItem(text=t) for t in texts])


def _ir(slides: list[SlideIR]) -> PresentationIR:
    return PresentationIR(variant="A", template_source_hash="x", slides=slides)


def _pad(slides: list[SlideIR], target: int = 10) -> list[SlideIR]:
    """Pad with non-text-only fillers so padding never silently extends (or
    starts) a text-only streak the test itself didn't set up."""
    next_index = max((s.slide_index for s in slides), default=-1) + 1
    padded = list(slides)
    while len(padded) < target:
        padded.append(
            _slide(
                next_index, LayoutType.KPI_DASHBOARD, [MetricCard(label="L", value="V")]
            )
        )
        next_index += 1
    return padded


# --------------------------------------------------------------------------- #
# EMPTY_CONTENT
# --------------------------------------------------------------------------- #


def test_empty_content_slide_is_flagged_critical():
    slide = _slide(0, LayoutType.CONTENT_1COL, [])
    ir = _ir(_pad([slide]))

    issues = run_visual_variety_audit(ir)

    empty_issues = [i for i in issues if i.issue_type == IssueType.EMPTY_CONTENT]
    assert len(empty_issues) == 1
    assert empty_issues[0].slide_index == 0
    assert empty_issues[0].severity == Severity.CRITICAL


def test_empty_title_slide_is_not_flagged():
    slide = _slide(0, LayoutType.TITLE_SLIDE, [])
    ir = _ir(_pad([slide]))

    issues = run_visual_variety_audit(ir)

    assert not any(i.issue_type == IssueType.EMPTY_CONTENT for i in issues)


def test_empty_section_header_slide_is_not_flagged():
    slide = _slide(0, LayoutType.SECTION_HEADER, [])
    ir = _ir(_pad([slide]))

    issues = run_visual_variety_audit(ir)

    assert not any(i.issue_type == IssueType.EMPTY_CONTENT for i in issues)


# --------------------------------------------------------------------------- #
# VISUAL_MONOTONY: text-only streaks
# --------------------------------------------------------------------------- #


def test_exactly_one_monotony_warning_per_streak_of_three():
    slides = [
        _slide(0, LayoutType.CONTENT_1COL, [_bullets("a")]),
        _slide(1, LayoutType.CONTENT_1COL, [_bullets("b")]),
        _slide(2, LayoutType.CONTENT_1COL, [_bullets("c")]),
        _slide(3, LayoutType.CONTENT_1COL, [_bullets("d")]),
        _slide(4, LayoutType.CONTENT_1COL, [_bullets("e")]),
        _slide(5, LayoutType.CONTENT_1COL, [_bullets("f")]),
        _slide(6, LayoutType.KPI_DASHBOARD, [MetricCard(label="L", value="V")]),
        _slide(7, LayoutType.CONTENT_1COL, [_bullets("g")]),
        _slide(8, LayoutType.CONTENT_1COL, [_bullets("h")]),
        _slide(9, LayoutType.CONTENT_1COL, [_bullets("i")]),
    ]
    ir = _ir(slides)

    issues = run_visual_variety_audit(ir)

    streak_issues = [
        i
        for i in issues
        if i.issue_type == IssueType.VISUAL_MONOTONY and "consecutive" in i.message
    ]
    # The check is `text_streak == 3` (an equality, not a threshold or modulo),
    # so a run of six consecutive text-only slides (0-5) fires exactly once,
    # at the slide where the streak first reaches 3 — it does not re-fire as
    # the streak keeps growing past 3. Slide 6 (KPI) resets the streak, and
    # the next three-slide run (7-9) fires again once, at slide 9.
    assert len(streak_issues) == 2
    assert [i.slide_index for i in streak_issues] == [2, 9]


def test_streak_resets_on_a_non_text_only_slide():
    slides = [
        _slide(0, LayoutType.CONTENT_1COL, [_bullets("a")]),
        _slide(1, LayoutType.CONTENT_1COL, [_bullets("b")]),
        _slide(2, LayoutType.KPI_DASHBOARD, [MetricCard(label="L", value="V")]),
        _slide(3, LayoutType.CONTENT_1COL, [_bullets("c")]),
        _slide(4, LayoutType.CONTENT_1COL, [_bullets("d")]),
    ]
    ir = _ir(_pad(slides))

    issues = run_visual_variety_audit(ir)

    assert not any(
        i.issue_type == IssueType.VISUAL_MONOTONY and "consecutive" in i.message
        for i in issues
    )


# --------------------------------------------------------------------------- #
# Fewer than 3 component types across a >= 10 slide deck
# --------------------------------------------------------------------------- #


def test_deck_with_fewer_than_three_component_types_and_ten_plus_slides_warns():
    slides = [
        _slide(i, LayoutType.CONTENT_1COL, [_bullets(f"пункт {i}")]) for i in range(10)
    ]
    ir = _ir(slides)

    issues = run_visual_variety_audit(ir)

    variety_issues = [
        i
        for i in issues
        if i.issue_type == IssueType.VISUAL_MONOTONY and "component type" in i.message
    ]
    assert len(variety_issues) == 1
    assert variety_issues[0].severity == Severity.WARNING


def test_deck_with_three_or_more_component_types_does_not_warn_about_variety():
    slides = [
        _slide(0, LayoutType.KPI_DASHBOARD, [MetricCard(label="L", value="V")]),
        _slide(1, LayoutType.CONTENT_1COL, [_bullets("a")]),
    ]
    for i in range(2, 10):
        slides.append(_slide(i, LayoutType.CONTENT_1COL, [_bullets(f"пункт {i}")]))
    # inject a third component type
    slides[2] = _slide(
        2,
        LayoutType.TABLE_FOCUSED,
        [TableData(headers=["H"], rows=[["1"]])],
    )
    ir = _ir(slides)

    issues = run_visual_variety_audit(ir)

    assert not any(
        i.issue_type == IssueType.VISUAL_MONOTONY and "component type" in i.message
        for i in issues
    )
