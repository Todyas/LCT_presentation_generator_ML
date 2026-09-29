from __future__ import annotations

import re

from app.models.presentation_ir import (
    BulletBlock,
    ImagePlaceholder,
    PresentationIR,
    SlideIR,
)
from app.models.template_manifest import (
    LayoutManifest,
    LayoutType,
    PlaceholderType,
    TemplateManifest,
)

_CLOSING_NAME = re.compile(r"спасибо|thank|конец|контакт|(?<![a-zа-я])end(?![a-zа-я])", re.IGNORECASE)
_CLOSING_TITLE = re.compile(r"спасибо|thank|вопрос|question|контакт|contact", re.IGNORECASE)
_COVER_NAME = re.compile(r"титул|title slide|обложк|cover", re.IGNORECASE)

_CHROME = frozenset(
    {PlaceholderType.FOOTER, PlaceholderType.DATE, PlaceholderType.SLIDE_NUMBER}
)
_NON_CONTENT = _CHROME | {PlaceholderType.TITLE, PlaceholderType.PICTURE}
_SEMANTIC_TYPES = frozenset(
    {
        LayoutType.TABLE_FOCUSED,
        LayoutType.CHART_FOCUSED,
        LayoutType.KPI_DASHBOARD,
        LayoutType.PROCESS_TIMELINE,
        LayoutType.COMPARISON,
        LayoutType.QUOTE,
        LayoutType.CONTENT_2COL,
    }
)

_USAGE_DECAY = 0.75
_DIVERSITY_RATIO = 0.7
_MIN_RELAXED_AREA = 0.3
_MIN_PRIMARY_CANDIDATES = 4


def _is_closing_layout(layout: LayoutManifest) -> bool:
    return bool(_CLOSING_NAME.search(layout.layout_name))


def _slide_area(manifest: TemplateManifest) -> float:
    return float(manifest.slide_width_emu * manifest.slide_height_emu)


def _real_body_count(layout: LayoutManifest) -> int:
    return sum(
        1
        for s in layout.slots
        if s.placeholder_type == PlaceholderType.BODY and not s.inferred
    )


def _tiny_body_count(layout: LayoutManifest) -> int:
    return sum(
        1
        for s in layout.slots
        if s.placeholder_type == PlaceholderType.BODY
        and not s.inferred
        and (s.normalized.w < 0.18 or s.normalized.h < 0.10)
    )


def _picture_area(layout: LayoutManifest) -> float:
    return sum(
        s.normalized.w * s.normalized.h
        for s in layout.slots
        if s.placeholder_type == PlaceholderType.PICTURE and not s.inferred
    )


def _slot_based_area(layout: LayoutManifest) -> float:
    """Free-area estimate from placeholders, used until content_region is known."""
    area = 0.0
    for s in layout.slots:
        if s.placeholder_type in _NON_CONTENT:
            continue
        area += s.normalized.w * s.normalized.h * (0.5 if s.inferred else 1.0)
    if area == 0.0 and layout.slot_by_type(PlaceholderType.TITLE) is not None:
        # a title-only layout is an empty canvas the builder fills itself
        return 0.3
    return min(area, 0.8)


def _content_area(layout: LayoutManifest, manifest: TemplateManifest) -> float:
    region = layout.content_region
    if region is not None:
        return region.width_emu * region.height_emu / _slide_area(manifest)
    return _slot_based_area(layout)


def _fit_bonus(layout: LayoutManifest, slide: SlideIR, area: float) -> float:
    bullets = sum(1 for c in slide.components if isinstance(c, BulletBlock))
    visual = any(not isinstance(c, BulletBlock) for c in slide.components)
    body = _real_body_count(layout)
    bonus = 0.0
    if slide.layout_type == layout.layout_type and layout.layout_type in _SEMANTIC_TYPES:
        bonus += 0.15
    if bullets >= 2:
        bonus += 0.15 if body >= 2 else 0.0
    elif bullets == 1 and not visual:
        bonus += 0.15 if body >= 1 else 0.0
    if visual and area >= 0.3:
        bonus += 0.1
    if any(isinstance(c, ImagePlaceholder) for c in slide.components) and (
        layout.slot_by_type(PlaceholderType.PICTURE) is not None
    ):
        bonus += 0.15
    return bonus


def _score(
    layout: LayoutManifest,
    slide: SlideIR,
    manifest: TemplateManifest,
    usage: dict[int, int],
    *,
    is_last: bool,
) -> float:
    area = _content_area(layout, manifest)
    decoration = layout.decoration_coverage
    if decoration <= 0.0 and not any(
        isinstance(c, ImagePlaceholder) for c in slide.components
    ):
        decoration = _picture_area(layout) * 0.7
    score = (0.15 + min(area, 0.85)) * (1.0 - 0.8 * min(decoration, 1.0))
    score += _fit_bonus(layout, slide, area)
    if layout.slot_by_type(PlaceholderType.TITLE) is None:
        score *= 0.5
    if _tiny_body_count(layout) >= 2:
        score *= 0.3
    title_slot = layout.slot_by_type(PlaceholderType.TITLE)
    if (
        title_slot is not None
        and title_slot.normalized.y > 0.3
        and layout.layout_type != LayoutType.TITLE_SLIDE
    ):
        score *= 0.3
    if layout.layout_type in (LayoutType.SECTION_HEADER, LayoutType.QUOTE):
        score *= 0.6
    if is_last and _is_closing_layout(layout):
        score *= 1.0 if _CLOSING_TITLE.search(slide.title.text) else 0.4
    score *= _USAGE_DECAY ** usage.get(layout.layout_index, 0)
    return max(score, 0.001)


def _cover_candidates(manifest: TemplateManifest) -> list[LayoutManifest]:
    covers = [
        layout
        for layout in manifest.layouts
        if layout.layout_type == LayoutType.TITLE_SLIDE
    ]
    return [layout for layout in covers if not _is_closing_layout(layout)] or covers


def _cover_score(layout: LayoutManifest) -> float:
    score = 0.0
    if any(
        s.placeholder_type == PlaceholderType.TITLE and not s.inferred
        for s in layout.slots
    ):
        score += 1.0
    if any(
        s.placeholder_type in (PlaceholderType.SUBTITLE, PlaceholderType.BODY)
        and not s.inferred
        for s in layout.slots
    ):
        score += 0.5
    if _COVER_NAME.search(layout.layout_name):
        score += 1.0
    return score


def _content_candidates(
    manifest: TemplateManifest, cover_index: int | None, *, is_last: bool
) -> list[LayoutManifest]:
    def allowed(layout: LayoutManifest) -> bool:
        if layout.layout_index == cover_index:
            return False
        if _is_closing_layout(layout) and not is_last:
            return False
        return layout.layout_type != LayoutType.BLANK

    primary = [
        layout
        for layout in manifest.layouts
        if layout.layout_type != LayoutType.TITLE_SLIDE and allowed(layout)
    ]
    if len(primary) < _MIN_PRIMARY_CANDIDATES:
        # Real templates label most content layouts "title slide"; take the ones
        # whose measured free region proves they are not cover artwork.
        primary += [
            layout
            for layout in manifest.layouts
            if layout.layout_type == LayoutType.TITLE_SLIDE
            and allowed(layout)
            and not _COVER_NAME.search(layout.layout_name)
            and layout.content_region is not None
            and _content_area(layout, manifest) >= _MIN_RELAXED_AREA
            and layout.decoration_coverage <= 0.5
        ]
    if primary:
        return primary
    return [layout for layout in manifest.layouts if allowed(layout)]


def _choose(
    scored: list[tuple[float, int]], previous: int | None, avoid: set[int]
) -> int | None:
    pool = [(s, i) for s, i in scored if i not in avoid]
    if not pool:
        return None
    pool.sort(key=lambda t: (-t[0], t[1]))
    best_score, best_index = pool[0]
    if best_index != previous:
        return best_index
    for score, index in pool[1:]:
        if index != previous and score >= _DIVERSITY_RATIO * best_score:
            return index
    return best_index


def _pick(
    slide: SlideIR,
    manifest: TemplateManifest,
    *,
    position: int,
    total: int,
    usage: dict[int, int],
    previous: int | None,
    cover_index: int | None,
    avoid: set[int],
) -> int | None:
    candidates = _content_candidates(
        manifest, cover_index, is_last=position == total - 1
    )
    scored = [
        (
            _score(layout, slide, manifest, usage, is_last=position == total - 1),
            layout.layout_index,
        )
        for layout in candidates
    ]
    return _choose(scored, previous, avoid)


def _pick_cover(manifest: TemplateManifest, avoid: set[int]) -> int | None:
    candidates = [
        layout
        for layout in _cover_candidates(manifest)
        if layout.layout_index not in avoid
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda l: (_cover_score(l), -l.layout_index))
    return best.layout_index


def assign_layouts(ir: PresentationIR, manifest: TemplateManifest) -> PresentationIR:
    """Set `layout_index` on every slide so a deck does not reuse one layout."""

    total = len(ir.slides)
    cover_index = _pick_cover(manifest, set())
    usage: dict[int, int] = {}
    previous: int | None = None
    slides: list[SlideIR] = []
    for position, slide in enumerate(ir.slides):
        if position == 0:
            index = cover_index
        else:
            index = _pick(
                slide,
                manifest,
                position=position,
                total=total,
                usage=usage,
                previous=previous,
                cover_index=cover_index,
                avoid=set(),
            )
        slides.append(slide.model_copy(update={"layout_index": index}))
        if index is not None:
            usage[index] = usage.get(index, 0) + 1
        previous = index
    return ir.model_copy(update={"slides": slides})


def pick_alternative_layout(
    ir: PresentationIR,
    manifest: TemplateManifest,
    position: int,
    slide: SlideIR | None = None,
) -> int | None:
    """Best layout for slide `position` (0-based) other than its current one.

    `slide` is the content to score (e.g. a revised replacement); defaults to the
    slide currently at that position.
    """

    total = len(ir.slides)
    current = ir.slides[position].layout_index
    avoid = {current} if current is not None else set()
    if position == 0:
        return _pick_cover(manifest, avoid)

    others = [s.layout_index for i, s in enumerate(ir.slides) if i != position]
    usage: dict[int, int] = {}
    for index in others:
        if index is not None:
            usage[index] = usage.get(index, 0) + 1
    previous = ir.slides[position - 1].layout_index
    following = ir.slides[position + 1].layout_index if position + 1 < total else None
    cover_index = ir.slides[0].layout_index
    slide = slide or ir.slides[position]
    for blocked in (avoid | ({following} if following is not None else set()), avoid):
        picked = _pick(
            slide,
            manifest,
            position=position,
            total=total,
            usage=usage,
            previous=previous,
            cover_index=cover_index,
            avoid=blocked,
        )
        if picked is not None:
            return picked
    return None
