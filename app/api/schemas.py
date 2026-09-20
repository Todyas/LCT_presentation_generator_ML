from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JobCreatedResponse(BaseModel):
    job_id: str


class VariantResultDTO(BaseModel):
    variant: str
    pptx_available: bool
    pdf_available: bool
    preview_count: int
    audit_passed: bool | None
    error: str | None


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["PENDING", "RUNNING", "DONE", "FAILED"]
    variants: list[VariantResultDTO] = Field(default_factory=list)
    error: str | None = None
