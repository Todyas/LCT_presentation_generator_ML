"""Extended coverage for app/core/parser/template_parser.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.parser import template_parser as tp_module
from app.core.parser.template_parser import TemplateParser
from app.models.template_manifest import LayoutType
from tests.factories import asymmetric_manifest, poor_manifest, symmetric_manifest

# --------------------------------------------------------------------------- #
# Inferred slots from example slides
# --------------------------------------------------------------------------- #


def test_placeholderless_template_gains_inferred_slots_from_example_slides(
    tmp_cwd, placeholderless_template
):
    manifest = TemplateParser().parse(placeholderless_template)

    inferred_slots = [
        slot for layout in manifest.layouts for slot in layout.slots if slot.inferred
    ]
    assert inferred_slots
    assert all(slot.placeholder_idx < 0 for slot in inferred_slots)


def test_example_slide_text_is_not_copied_into_the_manifest(
    tmp_cwd, placeholderless_template
):
    manifest = TemplateParser().parse(placeholderless_template)

    # _inferred_slots only carries geometry (LayoutSlot has no text field at
    # all), so the example copy "Example heading 0"/"Example body content 0"
    # cannot appear anywhere in the manifest's own serialized form.
    dumped = manifest.model_dump_json()
    assert "Example heading" not in dumped
    assert "Example body content" not in dumped


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_second_parse_is_a_cache_hit_and_does_not_reopen_the_pptx(
    tmp_cwd, monkeypatch, generated_minimal_template
):
    original_presentation = tp_module.Presentation
    call_count = 0

    def _counting_presentation(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_presentation(*args, **kwargs)

    monkeypatch.setattr(tp_module, "Presentation", _counting_presentation)
    parser = TemplateParser()

    first = parser.parse(generated_minimal_template)
    second = parser.parse(generated_minimal_template)

    assert call_count == 1
    assert first == second


def test_cache_file_name_is_versioned_with_v3(tmp_cwd, generated_minimal_template):
    TemplateParser().parse(generated_minimal_template)

    cache_files = list(Path(".cache").glob("*.manifest.json"))
    assert len(cache_files) == 1
    assert ".v3.manifest.json" in cache_files[0].name


def test_parse_succeeds_even_when_the_cache_directory_is_not_writable(
    tmp_cwd, monkeypatch, generated_minimal_template
):
    original_mkdir = Path.mkdir

    def _raising_mkdir(self, *args, **kwargs):
        if self.name == ".cache":
            raise OSError("permission denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _raising_mkdir)

    manifest = TemplateParser().parse(generated_minimal_template)

    assert manifest.layouts
    assert not Path(".cache").exists()


# --------------------------------------------------------------------------- #
# find_layout_or_fallback: TITLE_SLIDE only ever lands on slide_index == 0
# --------------------------------------------------------------------------- #


_MANIFEST_FACTORIES = {
    "symmetric": symmetric_manifest,
    "poor": poor_manifest,
    "asymmetric": asymmetric_manifest,
}


@pytest.mark.parametrize("manifest_name", list(_MANIFEST_FACTORIES))
@pytest.mark.parametrize("layout_type", list(LayoutType))
def test_title_slide_layout_never_used_past_slide_zero(manifest_name, layout_type):
    manifest = _MANIFEST_FACTORIES[manifest_name]()

    resolved_at_zero = manifest.find_layout_or_fallback(layout_type, 0)
    resolved_later = manifest.find_layout_or_fallback(layout_type, 3)

    assert resolved_later.layout_type != LayoutType.TITLE_SLIDE
    if layout_type == LayoutType.TITLE_SLIDE:
        # every factory manifest here has a real TITLE_SLIDE layout, so
        # requesting it for the cover slide must actually resolve to it.
        assert resolved_at_zero.layout_type == LayoutType.TITLE_SLIDE


# --------------------------------------------------------------------------- #
# BrandProfile stays within its declared bounds for every synthetic template
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "template_fixture",
    [
        "default_template",
        "placeholderless_template",
        "asymmetric_template",
        "dark_template",
    ],
)
def test_brand_profile_within_bounds_for_every_synthetic_template(
    tmp_cwd, template_fixture, request
):
    template_path = request.getfixturevalue(template_fixture)

    manifest = TemplateParser().parse(template_path)

    assert 16 <= manifest.brand_profile.title_size_pt <= 54
    assert 10 <= manifest.brand_profile.body_size_pt <= 32
    assert len(manifest.brand_profile.sampled_colors) <= 12
