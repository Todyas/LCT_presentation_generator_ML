from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class JobCreatedResponse(BaseModel):
    job_id: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class JobSummaryDTO(BaseModel):
    job_id: str
    status: Literal["PENDING", "RUNNING", "DONE", "PARTIAL", "FAILED"]
    created_at: datetime
    template_filename: str
    job_type: Literal["GENERATE_DECK", "REVISE_SLIDE", "ACTIVATE_REVISION"]
    parent_job_id: str | None = None


class VariantResultDTO(BaseModel):
    variant: str
    pptx_available: bool
    pdf_available: bool
    html_available: bool = False
    preview_count: int
    audit_passed: bool | None
    error: str | None
    revision: int = 1
    export_state: Literal["READY", "STALE"] = "READY"


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["PENDING", "RUNNING", "DONE", "PARTIAL", "FAILED"]
    variants: list[VariantResultDTO] = Field(default_factory=list)
    error: str | None = None
    stage: str = "queued"
    progress: int = Field(default=0, ge=0, le=100)
    job_type: Literal["GENERATE_DECK", "REVISE_SLIDE", "ACTIVATE_REVISION"]
    parent_job_id: str | None = None
    output: dict | None = None


class SlideRevisionRequest(BaseModel):
    base_revision: int | None = Field(default=None, ge=1)
    comment: str = Field(default="", max_length=2000)
    shorten_text: bool = False
    make_action_title: bool = False
    change_layout: bool = False
    add_visual: bool = False
    regenerate: bool = False

    def as_instructions(self) -> str:
        instructions: list[str] = []
        if self.shorten_text:
            instructions.append("Сократи текст, сохранив все важные факты.")
        if self.make_action_title:
            instructions.append("Сделай заголовок выводом, а не названием темы.")
        if self.change_layout:
            instructions.append("Подбери другой подходящий макет из шаблона.")
        if self.add_visual:
            instructions.append(
                "Добавь уместный график, схему или визуальный блок, если данные это позволяют."
            )
        if self.regenerate:
            instructions.append(
                "Пересобери слайд, сохранив его исходную мысль и факты."
            )
        if self.comment.strip():
            instructions.append(self.comment.strip())
        return "\n".join(instructions) or "Улучши слайд, сохранив его смысл и факты."


class ActivateRevisionRequest(BaseModel):
    base_revision: int | None = Field(default=None, ge=1)
