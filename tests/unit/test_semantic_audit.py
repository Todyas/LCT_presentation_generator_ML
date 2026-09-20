from unittest.mock import AsyncMock

from app.core.agents.prompt_registry import PromptRegistry
from app.core.auditor.semantic_audit import (
    SemanticFinding,
    SemanticFindings,
    run_semantic_audit,
)
from app.models.audit_report import IssueType, Severity
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    PresentationIR,
    SlideIR,
    TitleComponent,
)
from app.models.template_manifest import LayoutType


def _ir() -> PresentationIR:
    slides = [
        SlideIR(
            slide_index=i,
            layout_type=LayoutType.CONTENT_1COL,
            title=TitleComponent(text=f"Slide {i} conclusion"),
            components=[BulletBlock(items=[BulletItem(text="A short bullet point")])],
        )
        for i in range(10)
    ]
    return PresentationIR(variant="A", template_source_hash="abc", slides=slides)


async def test_llm_failure_degrades_to_info_not_exception():
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(side_effect=TimeoutError("boom"))
    registry = PromptRegistry("skills")

    issues = await run_semantic_audit(
        ir=_ir(), brief="A brief.", llm=llm, registry=registry, model="qwen"
    )

    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.AUDIT_DEGRADED
    assert issues[0].severity == Severity.INFO


async def test_llm_success_maps_findings_to_issues():
    findings = SemanticFindings(
        findings=[
            SemanticFinding(
                issue_type="SEMANTIC_WEAK_TITLE", slide_index=1, message="weak title"
            ),
            SemanticFinding(
                issue_type="HALLUCINATED_NUMBER", slide_index=2, message="bad number"
            ),
        ]
    )
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(return_value=findings)
    registry = PromptRegistry("skills")

    issues = await run_semantic_audit(
        ir=_ir(), brief="A brief.", llm=llm, registry=registry, model="qwen"
    )

    assert len(issues) == 2
    assert issues[0].issue_type == IssueType.SEMANTIC_WEAK_TITLE
    assert issues[0].slide_index == 1
    assert issues[0].message == "weak title"
    assert issues[0].severity == Severity.WARNING
    assert issues[1].issue_type == IssueType.HALLUCINATED_NUMBER
    assert issues[1].slide_index == 2
    assert issues[1].message == "bad number"
    assert issues[1].severity == Severity.WARNING


async def test_empty_findings_returns_empty_list():
    llm = AsyncMock()
    llm.complete_structured = AsyncMock(return_value=SemanticFindings(findings=[]))
    registry = PromptRegistry("skills")

    issues = await run_semantic_audit(
        ir=_ir(), brief="A brief.", llm=llm, registry=registry, model="qwen"
    )

    assert issues == []
