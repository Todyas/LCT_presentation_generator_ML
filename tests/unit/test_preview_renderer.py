from app.core.exporter.preview_renderer import render_previews


def test_empty_or_corrupt_pdf_returns_empty_list(tmp_path):
    bogus_pdf = tmp_path / "corrupt.pdf"
    bogus_pdf.write_bytes(b"not a real pdf, just garbage bytes")

    result = render_previews(str(bogus_pdf), str(tmp_path))

    assert result == []
