from pathlib import Path

from PIL import Image

from app.core.exporter.html_exporter import export_html_viewer


def test_export_html_viewer_embeds_preview(tmp_path: Path):
    preview = tmp_path / "slide.png"
    Image.new("RGB", (20, 10), "white").save(preview)
    output = tmp_path / "deck.html"

    result = export_html_viewer([str(preview)], str(output), title="Demo deck")

    assert result == str(output)
    content = output.read_text(encoding="utf-8")
    assert "Demo deck" in content
    assert "data:image/png;base64," in content
    assert 'alt="Slide 1"' in content
