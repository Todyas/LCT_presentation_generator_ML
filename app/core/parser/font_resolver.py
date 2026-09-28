from __future__ import annotations

import platform
from pathlib import Path

_SYSTEM_FONT_DIRS = {
    "Windows": ["C:/Windows/Fonts"],
    "Linux": ["/usr/share/fonts", "/usr/local/share/fonts"],
}


def _normalize(name: str) -> str:
    return name.replace(" ", "").lower()


def _find_in_system_dirs(typeface_name: str) -> str | None:
    needle = _normalize(typeface_name)
    for dir_path in _SYSTEM_FONT_DIRS.get(platform.system(), []):
        root = Path(dir_path)
        if not root.exists():
            continue
        for font_file in root.rglob("*"):
            if font_file.suffix.lower() not in (".ttf", ".otf"):
                continue
            if needle in _normalize(font_file.stem):
                return str(font_file)
    return None


def resolve_font_path(
    typeface_name: str | None, fallback_dir: str = "docker/fonts"
) -> str:
    fallback = str(Path(fallback_dir) / "DejaVuSans.ttf")
    if not typeface_name:
        return fallback

    found = _find_in_system_dirs(typeface_name)
    return found or fallback
