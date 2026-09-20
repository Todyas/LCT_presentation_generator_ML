from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class IssueType(str, Enum):
    COLLISION = "COLLISION"
    OVERFLOW = "OVERFLOW"
    CONTRAST = "CONTRAST"
    DENSITY_BULLETS = "DENSITY_BULLETS"
    DENSITY_WORDS = "DENSITY_WORDS"
    TABLE_SIZE = "TABLE_SIZE"
    PLACEHOLDER_TEXT = "PLACEHOLDER_TEXT"
    SEMANTIC_WEAK_TITLE = "SEMANTIC_WEAK_TITLE"
    HALLUCINATED_NUMBER = "HALLUCINATED_NUMBER"
    LANGUAGE_MIX = "LANGUAGE_MIX"
    AUDIT_DEGRADED = "AUDIT_DEGRADED"


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class AuditIssue(BaseModel):
    issue_type: IssueType
    severity: Severity
    slide_index: int
    message: str
    bbox: BBox | None = None
    shape_ids: list[str] = Field(default_factory=list)
    auto_fixable: bool = False
    fixed: bool = False


class AuditReport(BaseModel):
    variant: Literal["A", "B", "C"]
    issues: list[AuditIssue] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def passed(self) -> bool:
        return not any(
            i.severity == Severity.CRITICAL and not i.fixed for i in self.issues
        )

    @property
    def critical_unfixed(self) -> list[AuditIssue]:
        return [
            i for i in self.issues if i.severity == Severity.CRITICAL and not i.fixed
        ]
