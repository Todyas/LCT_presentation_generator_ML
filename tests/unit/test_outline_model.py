import pytest
from pydantic import ValidationError

from app.models.outline import Outline, OutlineItem
from app.models.template_manifest import LayoutType


def _item(index: int) -> OutlineItem:
    return OutlineItem(
        slide_index=index,
        working_title=f"Slide {index}",
        key_message=f"Key message {index}",
        suggested_layout_type=LayoutType.CONTENT_1COL,
        content_hint=f"Content hint {index}",
    )


def test_outline_rejects_out_of_range_length():
    items = [_item(i) for i in range(16)]
    with pytest.raises(ValidationError):
        Outline(variant="A", items=items)
