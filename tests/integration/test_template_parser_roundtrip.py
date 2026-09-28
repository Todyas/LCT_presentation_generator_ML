import hashlib
from pathlib import Path
from unittest import mock

import pytest

from app.core.parser.template_parser import TemplateParseError, TemplateParser
from app.models.template_manifest import LayoutType

FIXTURE_PATH = "tests/fixtures/templates/generated_minimal.pptx"


def _cache_path_for(pptx_path: str) -> Path:
    file_bytes = Path(pptx_path).read_bytes()
    source_hash = hashlib.sha256(file_bytes).hexdigest()
    return Path(".cache") / f"{source_hash}.v3.manifest.json"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache_path = _cache_path_for(FIXTURE_PATH)
    cache_path.unlink(missing_ok=True)
    yield
    cache_path.unlink(missing_ok=True)


def test_parse_happy_path_has_title_slide_layout():
    manifest = TemplateParser().parse(FIXTURE_PATH)

    assert len(manifest.layouts) > 0
    assert any(
        layout.layout_type == LayoutType.TITLE_SLIDE for layout in manifest.layouts
    )


def test_second_parse_uses_cache_and_skips_zip_read():
    parser = TemplateParser()
    first = parser.parse(FIXTURE_PATH)

    with (
        mock.patch("app.core.parser.template_parser.zipfile.ZipFile") as mock_zip,
        mock.patch("app.core.parser.template_parser.Presentation") as mock_prs,
    ):
        second = parser.parse(FIXTURE_PATH)

    mock_zip.assert_not_called()
    mock_prs.assert_not_called()
    assert second == first


def test_parse_non_pptx_file_raises_template_parse_error(tmp_path):
    bogus = tmp_path / "not_a_real.pptx"
    bogus.write_text("this is not a pptx file")

    with pytest.raises(TemplateParseError):
        TemplateParser().parse(str(bogus))
