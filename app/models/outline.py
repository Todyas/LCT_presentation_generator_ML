from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.template_manifest import LayoutType


class OutlineItem(BaseModel):
    slide_index: int = Field(ge=0)
    working_title: str = Field(min_length=1, max_length=120)
    key_message: str = Field(min_length=1, max_length=300)
    suggested_layout_type: LayoutType
    content_hint: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def fill_optional_content_hint(self) -> OutlineItem:
        # content_hint helps the next LLM stage but is not presentation data.
        # A provider occasionally returns an empty string here; falling back to
        # the key message is safer than failing all three deck variants.
        if not self.content_hint.strip():
            self.content_hint = self.key_message
        return self


class Outline(BaseModel):
    variant: Literal["A", "B", "C"]
    items: list[OutlineItem] = Field(min_length=10, max_length=15)
