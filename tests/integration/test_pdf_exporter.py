import asyncio
import shutil
from pathlib import Path

import pytest

from app.core.exporter.pdf_exporter import convert_to_pdf

pytestmark = pytest.mark.docker

if shutil.which("soffice") is None:
    pytest.skip("soffice (LibreOffice) not installed on this machine", allow_module_level=True)

FIXTURE_PATH = "tests/fixtures/templates/generated_minimal.pptx"


def test_convert_produces_pdf_file(tmp_path):
    result = asyncio.run(convert_to_pdf(FIXTURE_PATH, str(tmp_path)))

    assert Path(result).exists()
    assert Path(result).stat().st_size > 0


def test_two_parallel_conversions_do_not_collide(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    copy_a = dir_a / "template_a.pptx"
    copy_b = dir_b / "template_b.pptx"
    shutil.copyfile(FIXTURE_PATH, copy_a)
    shutil.copyfile(FIXTURE_PATH, copy_b)

    async def _run() -> tuple[str, str]:
        return await asyncio.gather(
            convert_to_pdf(str(copy_a), str(dir_a)),
            convert_to_pdf(str(copy_b), str(dir_b)),
        )

    result_a, result_b = asyncio.run(_run())

    assert Path(result_a).exists() and Path(result_a).stat().st_size > 0
    assert Path(result_b).exists() and Path(result_b).stat().st_size > 0
    assert result_a != result_b
