from __future__ import annotations

from pdf2image import convert_from_path


def render_previews(pdf_path: str, output_dir: str, dpi: int = 96) -> list[str]:
    try:
        paths = convert_from_path(
            pdf_path, dpi=dpi, output_folder=output_dir, fmt="png", paths_only=True
        )
    except Exception:  # noqa: BLE001 — previews are a nice-to-have, never load-bearing for the deliverable
        return []
    return list(paths)
