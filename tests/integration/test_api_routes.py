import io
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.main import app
from app.core.parser.template_parser import TemplateParser
from app.models.audit_report import AuditIssue, AuditReport, IssueType, Severity
from app.models.presentation_ir import (
    BulletBlock,
    BulletItem,
    PresentationIR,
    SlideIR,
    TitleComponent,
)
from app.models.template_manifest import LayoutType
from app.pipeline.jobs import Job, JobStatus, JobType
from app.pipeline.orchestrator import ResultPackage, VariantResult

FIXTURE_PATH = "tests/fixtures/templates/generated_minimal.pptx"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_job_store():
    routes.job_store.clear()
    yield
    routes.job_store.clear()


def test_generate_rejects_invalid_pptx_upload():
    response = client.post(
        "/generate",
        files={
            "template": (
                "fake.pptx",
                b"this is not a zip archive",
                "application/octet-stream",
            )
        },
        data={"brief": "A sufficiently long brief for validation."},
    )

    assert response.status_code == 422
    assert routes.job_store.list_all() == []


def test_generate_accepts_valid_pptx_and_returns_job_id():
    with mock.patch("app.api.routes.run_generation_job", new=mock.AsyncMock()):
        raw_bytes = Path(FIXTURE_PATH).read_bytes()
        response = client.post(
            "/generate",
            files={
                "template": (
                    "template.pptx",
                    raw_bytes,
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            },
            data={"brief": "A sufficiently long brief for validation."},
        )

    assert response.status_code == 202
    assert "job_id" in response.json()


def test_get_job_returns_404_for_unknown_id():
    response = client.get("/jobs/does-not-exist")

    assert response.status_code == 404


def test_get_job_reflects_completed_state():
    variant_result = VariantResult(
        variant="A",
        pptx_path="/fake/variant_A.pptx",
        pdf_path="/fake/variant_A.pdf",
        preview_paths=["/fake/preview_0.png"],
        audit_report=AuditReport(variant="A", issues=[]),
    )
    job = Job(
        job_id="seeded-job",
        status=JobStatus.DONE,
        result=ResultPackage(variants={"A": variant_result}),
    )
    routes.job_store.put(job)

    response = client.get("/jobs/seeded-job")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DONE"
    assert body["variants"][0]["pptx_available"] is True
    assert body["variants"][0]["audit_passed"] is True


def test_analyze_template_returns_template_dna():
    raw_bytes = Path(FIXTURE_PATH).read_bytes()

    response = client.post(
        "/templates/analyze",
        files={
            "template": (
                "template.pptx",
                raw_bytes,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert body["layout_count"] > 0
    assert body["master_count"] > 0
    assert body["colors"]
    assert body["fonts"]


def test_analyze_template_degrades_when_cache_dir_is_not_writable(monkeypatch):
    # Regression test for a production 500: the container runs as a non-root
    # user that cannot create TemplateParser's ".cache" directory. Parsing
    # must still succeed, just without caching.
    original_mkdir = Path.mkdir

    def _raising_mkdir(self, *args, **kwargs):
        if self.name == ".cache":
            raise PermissionError(13, "Permission denied", str(self))
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", _raising_mkdir)

    raw_bytes = Path(FIXTURE_PATH).read_bytes()
    response = client.post(
        "/templates/analyze",
        files={
            "template": (
                "template.pptx",
                raw_bytes,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert body["layout_count"] > 0


def test_analyze_template_returns_422_for_unparseable_pptx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", "not valid presentation xml <<<")
        zf.writestr("[Content_Types].xml", "<Types/>")

    response = client.post(
        "/templates/analyze",
        files={
            "template": (
                "corrupt.pptx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert response.status_code == 422


def _presentation_ir() -> PresentationIR:
    return PresentationIR(
        variant="A",
        template_source_hash="fixture",
        slides=[
            SlideIR(
                slide_index=i,
                layout_type=LayoutType.CONTENT_1COL,
                title=TitleComponent(text=f"Слайд {i + 1}"),
                components=[BulletBlock(items=[BulletItem(text="Короткий тезис")])],
            )
            for i in range(10)
        ],
    )


def test_result_exposes_slides_previews_and_audit(tmp_path):
    preview = tmp_path / "slide.png"
    preview.write_bytes(b"png")
    manifest = TemplateParser().parse(FIXTURE_PATH)
    issue = AuditIssue(
        issue_type=IssueType.OVERFLOW,
        severity=Severity.CRITICAL,
        slide_index=0,
        message="overflow",
    )
    variant_result = VariantResult(
        variant="A",
        pptx_path="/fake/variant_A.pptx",
        preview_paths=[str(preview)],
        audit_report=AuditReport(variant="A", issues=[issue]),
        presentation_ir=_presentation_ir(),
    )
    routes.job_store.put(
        Job(
            job_id="result-job",
            status=JobStatus.DONE,
            result=ResultPackage(
                variants={"A": variant_result},
                template_manifest=manifest,
            ),
        )
    )

    response = client.get("/jobs/result-job/result")

    assert response.status_code == 200
    variant = response.json()["variants"][0]
    assert variant["name"] == "Executive"
    assert variant["slides"][0]["preview_url"].endswith("/A/1")
    assert variant["slides"][0]["issues"][0]["issue_type"] == "OVERFLOW"
    assert len(variant["slides"]) == 10


def test_running_job_cannot_be_deleted():
    routes.job_store.put(Job(job_id="running", status=JobStatus.RUNNING))

    response = client.delete("/jobs/running")

    assert response.status_code == 409
    assert routes.job_store.get("running") is not None


def test_completed_job_events_returns_terminal_sse():
    routes.job_store.put(
        Job(
            job_id="events-job",
            status=JobStatus.DONE,
            stage="completed",
            progress=100,
        )
    )

    response = client.get("/jobs/events-job/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"progress": 100' in response.text


def test_revise_slide_creates_background_job_and_marks_export_stale():
    manifest = TemplateParser().parse(FIXTURE_PATH)
    initial = VariantResult(
        variant="A",
        audit_report=AuditReport(variant="A", issues=[]),
        presentation_ir=_presentation_ir(),
    )
    routes.job_store.put(
        Job(
            job_id="revise-job",
            status=JobStatus.DONE,
            result=ResultPackage(
                variants={"A": initial},
                template_manifest=manifest,
            ),
            template_path=FIXTURE_PATH,
            brief="Подробный бриф презентации",
        )
    )

    with mock.patch(
        "app.api.routes.run_revision_job",
        new=mock.AsyncMock(),
    ):
        response = client.post(
            "/jobs/revise-job/slides/A/2/revise",
            json={
                "base_revision": 1,
                "shorten_text": True,
                "comment": "Сделай короче",
            },
        )

    assert response.status_code == 202
    child = routes.job_store.get(response.json()["job_id"])
    assert child.job_type == JobType.REVISE_SLIDE
    assert child.parent_job_id == "revise-job"
    assert child.slide_position == 2
    assert (
        routes.job_store.get("revise-job").result.variants["A"].export_state == "STALE"
    )


def test_revise_slide_rejects_stale_base_revision():
    manifest = TemplateParser().parse(FIXTURE_PATH)
    routes.job_store.put(
        Job(
            job_id="revision-conflict",
            status=JobStatus.DONE,
            result=ResultPackage(
                variants={
                    "A": VariantResult(
                        variant="A",
                        presentation_ir=_presentation_ir(),
                        revision=3,
                    )
                },
                template_manifest=manifest,
            ),
        )
    )

    response = client.post(
        "/jobs/revision-conflict/slides/A/1/revise",
        json={"base_revision": 2, "comment": "Уточни вывод"},
    )

    assert response.status_code == 409
