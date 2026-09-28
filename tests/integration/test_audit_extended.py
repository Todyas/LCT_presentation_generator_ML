"""Integration coverage for app/core/auditor/audit_runner.run_full_audit:
real PptxBuilder output audited end-to-end, with ScriptedLLM for the
semantic-audit LLM call.
"""

from __future__ import annotations

import pytest

from app.core.agents.prompt_registry import PromptRegistry
from app.core.auditor.audit_runner import run_full_audit
from app.core.auditor.semantic_audit import SemanticFinding, SemanticFindings
from app.core.builder.pptx_builder import PptxBuilder
from app.core.parser.template_parser import TemplateParser
from app.models.audit_report import IssueType, Severity
from app.models.presentation_ir import PresentationIR
from app.models.template_manifest import LayoutType
from tests.conftest import SKILLS_DIR
from tests.factories import make_slide

REGISTRY = PromptRegistry(str(SKILLS_DIR))


def _clean_ir(manifest_hash: str, *, start_index: int = 0) -> PresentationIR:
    slides = [
        make_slide(
            LayoutType.CONTENT_1COL, slide_index=start_index + i, title=f"Вывод {i}"
        )
        for i in range(10)
    ]
    return PresentationIR(
        variant="A", template_source_hash=manifest_hash, slides=slides
    )


async def test_semantic_llm_exception_degrades_to_one_info_issue_and_passed_stays_true(
    tmp_cwd, scripted_llm, default_template
):
    manifest = TemplateParser().parse(default_template)
    ir = _clean_ir(manifest.source_hash)
    build_result = PptxBuilder().build(default_template, manifest, ir)
    # SemanticFindings is left unscripted on purpose: ScriptedLLM raises
    # AssertionError for it, which run_semantic_audit's broad except turns
    # into a degrade, exactly like a real LLM timeout/connection error would.

    report = await run_full_audit(
        build_result, ir, manifest, "Бриф без чисел.", scripted_llm, REGISTRY, "qwen"
    )

    degraded = [i for i in report.issues if i.issue_type == IssueType.AUDIT_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].severity == Severity.INFO
    assert degraded[0].slide_index == -1
    assert report.passed is True


async def test_semantic_findings_are_mapped_to_warning_severity(
    tmp_cwd, scripted_llm, default_template
):
    manifest = TemplateParser().parse(default_template)
    ir = _clean_ir(manifest.source_hash)
    build_result = PptxBuilder().build(default_template, manifest, ir)
    scripted_llm.script(
        SemanticFindings,
        SemanticFindings(
            findings=[
                SemanticFinding(
                    issue_type="SEMANTIC_WEAK_TITLE",
                    slide_index=0,
                    message="Слабый заголовок",
                )
            ]
        ),
    )

    report = await run_full_audit(
        build_result, ir, manifest, "Бриф без чисел.", scripted_llm, REGISTRY, "qwen"
    )

    semantic_issues = [
        i for i in report.issues if i.issue_type == IssueType.SEMANTIC_WEAK_TITLE
    ]
    assert len(semantic_issues) == 1
    assert semantic_issues[0].severity == Severity.WARNING
    assert semantic_issues[0].message == "Слабый заголовок"


@pytest.mark.xfail(
    strict=True,
    reason="BUG (ARCHITECTURE.md §12.8): contrast_audit uses the slide's "
    "position in the built .pptx (audit_runner.py:72, "
    "`enumerate(prs.slides)`), while geometry/density/visual audits use "
    "SlideIR.slide_index (audit_runner.py:35). When the outline numbers "
    "slides starting at 1 instead of 0, a contrast issue is attributed to "
    "the wrong slide.",
)
async def test_contrast_issue_slide_index_matches_the_irs_slide_index_not_pptx_position(
    tmp_cwd, scripted_llm, dark_template
):
    manifest = TemplateParser().parse(dark_template)
    # slide_index values start at 1 (as a real outline sometimes does), so
    # slide_index=5 sits at pptx position 4 (0-based).
    ir = _clean_ir(manifest.source_hash, start_index=1)
    build_result = PptxBuilder().build(dark_template, manifest, ir)

    report = await run_full_audit(
        build_result, ir, manifest, "Бриф без чисел.", scripted_llm, REGISTRY, "qwen"
    )

    contrast_issues = [i for i in report.issues if i.issue_type == IssueType.CONTRAST]
    assert (
        contrast_issues
    )  # the near-black master background must fail WCAG for every slide

    contrast_slide_indices = {issue.slide_index for issue in contrast_issues}
    expected_slide_indices = {
        slide.slide_index for slide in ir.slides
    }  # {1, 2, ..., 10}
    # currently contrast_slide_indices comes out as {0, 1, ..., 9} (the pptx
    # position), one less than the IR's own numbering for every slide.
    assert contrast_slide_indices == expected_slide_indices
