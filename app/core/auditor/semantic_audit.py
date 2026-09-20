from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.agents.llm_client import LLMClient
from app.core.agents.prompt_registry import PromptRegistry
from app.models.audit_report import AuditIssue, IssueType, Severity
from app.models.presentation_ir import PresentationIR


class SemanticFinding(BaseModel):
    issue_type: Literal["SEMANTIC_WEAK_TITLE", "HALLUCINATED_NUMBER", "LANGUAGE_MIX"]
    slide_index: int
    message: str


class SemanticFindings(BaseModel):
    findings: list[SemanticFinding] = Field(default_factory=list)


async def run_semantic_audit(
    ir: PresentationIR,
    brief: str,
    llm: LLMClient,
    registry: PromptRegistry,
    model: str,
) -> list[AuditIssue]:
    spec = registry.load("semantic_audit", "latest")
    try:
        user_prompt = spec.user_template.format(
            brief=brief, deck_json=ir.model_dump_json()
        )
        result = await llm.complete_structured(
            model=model,
            system_prompt=spec.system_prompt,
            user_prompt=user_prompt,
            response_model=SemanticFindings,
            model_params=spec.model_params,
        )
    except Exception as exc:  # noqa: BLE001 — any LLM failure mode must degrade, never crash the pipeline
        return [
            AuditIssue(
                issue_type=IssueType.AUDIT_DEGRADED,
                severity=Severity.INFO,
                slide_index=-1,
                message=f"semantic audit skipped: {exc}",
                auto_fixable=False,
            )
        ]

    return [
        AuditIssue(
            issue_type=IssueType(f.issue_type),
            severity=Severity.WARNING,
            slide_index=f.slide_index,
            message=f.message,
            auto_fixable=False,
        )
        for f in result.findings
    ]
