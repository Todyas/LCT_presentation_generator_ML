from __future__ import annotations

from app.models.audit_report import AuditIssue, IssueType, Severity
from app.models.presentation_ir import BulletBlock, PresentationIR
from app.models.template_manifest import LayoutType


def run_visual_variety_audit(ir: PresentationIR) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    text_streak = 0
    component_kinds: set[str] = set()
    for slide in ir.slides:
        component_kinds.update(component.type for component in slide.components)
        if not slide.components and slide.layout_type not in {
            LayoutType.TITLE_SLIDE,
            LayoutType.SECTION_HEADER,
        }:
            issues.append(
                AuditIssue(
                    issue_type=IssueType.EMPTY_CONTENT,
                    severity=Severity.CRITICAL,
                    slide_index=slide.slide_index,
                    message="Content slide has no visual or textual component",
                )
            )
        text_only = bool(slide.components) and all(
            isinstance(component, BulletBlock) for component in slide.components
        )
        text_streak = text_streak + 1 if text_only else 0
        if text_streak == 3:
            issues.append(
                AuditIssue(
                    issue_type=IssueType.VISUAL_MONOTONY,
                    severity=Severity.WARNING,
                    slide_index=slide.slide_index,
                    message="Three consecutive slides use only bullet text",
                    auto_fixable=True,
                )
            )
    if len(ir.slides) >= 10 and len(component_kinds) < 3:
        issues.append(
            AuditIssue(
                issue_type=IssueType.VISUAL_MONOTONY,
                severity=Severity.WARNING,
                slide_index=0,
                message=f"Deck uses only {len(component_kinds)} component type(s); expected at least 3",
                auto_fixable=True,
            )
        )
    return issues
