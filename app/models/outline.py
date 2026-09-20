from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.template_manifest import LayoutType


class OutlineItem(BaseModel):
    slide_index: int = Field(ge=0)
    working_title: str = Field(min_length=1, max_length=120)
    key_message: str = Field(min_length=1, max_length=300)
    suggested_layout_type: LayoutType
    content_hint: str = Field(min_length=1, max_length=500)


class Outline(BaseModel):
    variant: Literal["A", "B", "C"]
    items: list[OutlineItem] = Field(min_length=10, max_length=15)
