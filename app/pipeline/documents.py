from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ALLOWED_SUFFIXES = (".pdf", ".md")
SOURCES_FILENAME = "sources.txt"


class DocumentError(ValueError):
    """A supplied document cannot be used; the message is safe to show to the user."""


@dataclass(frozen=True)
class SourceDocument:
    filename: str
    text: str


def extract_document_text(filename: str, raw_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".md":
        text = _decode_markdown(raw_bytes)
    elif suffix == ".pdf":
        text = _extract_pdf_text(filename, raw_bytes)
    else:
        raise DocumentError(f"{filename}: поддерживаются только .pdf и .md")
    text = text.strip()
    if not text:
        raise DocumentError(f"{filename}: в документе не найден текст")
    return text


def _decode_markdown(raw_bytes: bytes) -> str:
    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1251", errors="replace")


def _extract_pdf_text(filename: str, raw_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(raw_bytes))
        if reader.is_encrypted:
            raise DocumentError(f"{filename}: PDF защищён паролем")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except DocumentError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError) as exc:
        raise DocumentError(f"{filename}: не удалось прочитать PDF") from exc
    if not text.strip():
        raise DocumentError(f"{filename}: в PDF нет текстового слоя (похоже на скан)")
    return text


def build_source_context(documents: list[SourceDocument], max_chars: int) -> str:
    """Join documents into one block, giving each an equal share of the budget."""

    if not documents:
        return ""
    per_document = max(500, max_chars // len(documents))
    parts = []
    for document in documents:
        text = document.text
        if len(text) > per_document:
            text = text[:per_document].rstrip() + " […]"
        parts.append(f"[Документ: {document.filename}]\n{text}")
    return "\n\n".join(parts)


def brief_with_sources(brief: str, storage_dir: Path | str) -> str:
    sources_path = Path(storage_dir) / SOURCES_FILENAME
    if not sources_path.is_file():
        return brief
    sources = sources_path.read_text(encoding="utf-8").strip()
    if not sources:
        return brief
    return (
        f"{brief}\n\nИСХОДНЫЕ МАТЕРИАЛЫ (факты, цифры и названия для презентации "
        f"бери отсюда):\n{sources}"
    )
