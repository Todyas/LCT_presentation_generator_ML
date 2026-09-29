from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.main import app
from app.pipeline.documents import (
    DocumentError,
    SourceDocument,
    brief_with_sources,
    build_source_context,
    extract_document_text,
)

client = TestClient(app)
TEMPLATE = Path("tests/fixtures/templates/generated_minimal.pptx")


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objects) + 1,
        xref,
    )
    return out


def test_extract_markdown_and_pdf():
    assert extract_document_text("notes.md", "# Итоги\nВыручка 42".encode()) == (
        "# Итоги\nВыручка 42"
    )
    assert "Revenue 42" in extract_document_text("r.PDF", make_pdf("Revenue 42"))


def test_extract_rejects_bad_documents():
    with pytest.raises(DocumentError):
        extract_document_text("a.docx", b"x")
    with pytest.raises(DocumentError):
        extract_document_text("a.pdf", b"not a pdf")
    with pytest.raises(DocumentError):
        extract_document_text("a.md", b"   \n")


def test_source_context_splits_budget_and_brief_reads_it(tmp_path):
    docs = [SourceDocument("a.md", "a" * 5000), SourceDocument("b.md", "short")]
    context = build_source_context(docs, max_chars=2000)
    assert "[Документ: a.md]" in context and "short" in context
    assert len(context) < 2400
    (tmp_path / "sources.txt").write_text(context, encoding="utf-8")
    assert "short" in brief_with_sources("brief", tmp_path)
    assert brief_with_sources("brief", tmp_path / "missing") == "brief"


@pytest.fixture(autouse=True)
def _clear_jobs():
    routes.job_store.clear()
    yield
    routes.job_store.clear()


def _generate(documents, tmp_path):
    settings = routes.get_settings().model_copy(update={"storage_dir": str(tmp_path)})
    app.dependency_overrides[routes.get_settings] = lambda: settings
    try:
        with mock.patch("app.api.routes.run_generation_job", new=mock.AsyncMock()):
            return client.post(
                "/generate",
                files=[("template", ("t.pptx", TEMPLATE.read_bytes()))] + documents,
                data={"brief": "A sufficiently long brief for validation."},
            )
    finally:
        app.dependency_overrides.clear()


def test_generate_stores_extracted_documents(tmp_path):
    response = _generate(
        [
            ("documents", ("notes.md", b"Fact: churn fell to 7 percent")),
            ("documents", ("r.pdf", make_pdf("Revenue 42"))),
        ],
        tmp_path,
    )
    assert response.status_code == 202
    sources = (tmp_path / response.json()["job_id"] / "sources.txt").read_text(
        encoding="utf-8"
    )
    assert "churn fell to 7 percent" in sources and "Revenue 42" in sources


def test_generate_without_documents_still_works(tmp_path):
    response = _generate([], tmp_path)
    assert response.status_code == 202
    assert not (tmp_path / response.json()["job_id"] / "sources.txt").exists()


def test_generate_rejects_unreadable_and_too_many_documents(tmp_path):
    bad = _generate([("documents", ("x.pdf", b"garbage"))], tmp_path)
    assert bad.status_code == 422
    many = _generate([("documents", (f"{i}.md", b"text")) for i in range(11)], tmp_path)
    assert many.status_code == 422
    assert routes.job_store.list_all() == []
