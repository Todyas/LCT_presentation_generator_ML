from __future__ import annotations

import base64
import html
from pathlib import Path


def export_html_viewer(
    preview_paths: list[str], output_path: str, title: str = "Presentation"
) -> str:
    """Create a self-contained HTML viewer from rendered slide previews."""

    slides: list[str] = []
    for index, preview_path in enumerate(preview_paths, start=1):
        path = Path(preview_path)
        if not path.exists():
            continue
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        slides.append(
            '<section class="slide">'
            f'<img src="data:{mime};base64,{encoded}" alt="Slide {index}">'
            f"<span>{index}</span>"
            "</section>"
        )

    safe_title = html.escape(title)
    document = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin: 0; padding: 32px; background: #eef1f6; font-family: sans-serif; }}
    main {{ display: grid; gap: 32px; max-width: 1280px; margin: auto; }}
    .slide {{ position: relative; background: white; box-shadow: 0 8px 30px #0002; }}
    .slide img {{ display: block; width: 100%; height: auto; }}
    .slide span {{ position: absolute; right: 12px; bottom: 8px; color: #667085; }}
  </style>
</head>
<body><main>{"".join(slides)}</main></body>
</html>
"""
    target = Path(output_path)
    target.write_text(document, encoding="utf-8")
    return str(target)
