"""End-to-end run against real files: reads a template .pptx and a brief .pdf
from samples/input/, runs the real LLM pipeline (narrative_architect ->
slot_filler -> builder -> auditor -> exporter) via the actual orchestrator,
and writes all 3 variants to samples/output/generated/.

Requires a working LLM endpoint in .env (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY).

Usage:
    uv run python scripts/generate_from_samples.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader

from app.config import get_settings
from app.pipeline.orchestrator import Dependencies, generate_deck

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = REPO_ROOT / "samples" / "input"
OUTPUT_DIR = REPO_ROOT / "samples" / "output" / "generated"

MAX_BRIEF_CHARS = 6000
_LIBREOFFICE_DIR = r"C:\Program Files\LibreOffice\program"


def _ensure_soffice_on_path() -> None:
    if shutil.which("soffice") is not None:
        return
    if Path(_LIBREOFFICE_DIR).exists():
        os.environ["PATH"] += os.pathsep + _LIBREOFFICE_DIR


def _find_one(pattern: str) -> Path:
    # PptxBuilder writes its built_<variant>.pptx output next to the source template,
    # i.e. into this same input directory — exclude those, and Office's own "~$" lock
    # files for anything currently open, from the template lookup.
    matches = sorted(
        p for p in INPUT_DIR.glob(pattern)
        if not p.name.startswith("built_") and not p.name.startswith("~$")
    )
    if not matches:
        raise FileNotFoundError(f"no file matching {pattern!r} found in {INPUT_DIR}")
    if len(matches) > 1:
        raise ValueError(
            f"expected exactly one {pattern!r} in {INPUT_DIR}, found {len(matches)}: "
            f"{[m.name for m in matches]}"
        )
    return matches[0]


def _extract_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages_text).strip()
    if not text:
        raise ValueError(f"no extractable text in {pdf_path} (scanned/image-only PDF?)")
    if len(text) > MAX_BRIEF_CHARS:
        print(f"[warn] brief text is {len(text)} chars, truncating to {MAX_BRIEF_CHARS}")
        text = text[:MAX_BRIEF_CHARS]
    return text


async def main() -> None:
    _ensure_soffice_on_path()
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    template_path = _find_one("*.pptx")
    pdf_path = _find_one("*.pdf")
    print(f"template: {template_path.name}")
    print(f"brief pdf: {pdf_path.name}")

    brief_text = _extract_pdf_text(pdf_path)
    print(f"extracted brief: {len(brief_text)} chars")

    settings = get_settings()
    print(f"LLM endpoint: {settings.llm_base_url} | model: {settings.llm_model}")
    deps = Dependencies(settings)

    t0 = time.monotonic()
    result = await generate_deck(brief_text, str(template_path), deps)
    elapsed = time.monotonic() - t0
    print(f"\npipeline finished in {elapsed:.1f}s\n")

    summary = {}
    for variant, vr in result.variants.items():
        variant_dir = OUTPUT_DIR / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        entry: dict = {"error": vr.error}

        if vr.error is not None:
            print(f"[{variant}] FAILED: {vr.error}")
            summary[variant] = entry
            continue

        pptx_dest = variant_dir / f"variant_{variant}.pptx"
        shutil.copyfile(vr.pptx_path, pptx_dest)
        entry["pptx_path"] = str(pptx_dest)

        if vr.pdf_path is not None:
            pdf_dest = variant_dir / f"variant_{variant}.pdf"
            shutil.copyfile(vr.pdf_path, pdf_dest)
            entry["pdf_path"] = str(pdf_dest)

        entry["preview_count"] = len(vr.preview_paths)

        if vr.audit_report is not None:
            (variant_dir / "audit_report.json").write_text(
                vr.audit_report.model_dump_json(indent=2), encoding="utf-8"
            )
            entry["audit_passed"] = vr.audit_report.passed
            entry["issue_count"] = len(vr.audit_report.issues)

        print(f"[{variant}] OK — pptx: {pptx_dest.name} | audit passed: {entry.get('audit_passed')} | issues: {entry.get('issue_count')}")
        summary[variant] = entry

    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nFull summary written to {OUTPUT_DIR / 'summary.json'}")


if __name__ == "__main__":
    asyncio.run(main())
