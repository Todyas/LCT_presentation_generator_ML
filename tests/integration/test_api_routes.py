from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.main import app
from app.models.audit_report import AuditReport
from app.pipeline.jobs import Job, JobStatus
from app.pipeline.orchestrator import ResultPackage, VariantResult

FIXTURE_PATH = "tests/fixtures/templates/generated_minimal.pptx"

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_job_store():
    routes.job_store._jobs.clear()
    yield
    routes.job_store._jobs.clear()


def test_generate_rejects_invalid_pptx_upload():
    response = client.post(
        "/generate",
        files={"template": ("fake.pptx", b"this is not a zip archive", "application/octet-stream")},
        data={"brief": "A sufficiently long brief for validation."},
    )

    assert response.status_code == 422
    assert len(routes.job_store._jobs) == 0


def test_generate_accepts_valid_pptx_and_returns_job_id():
    with mock.patch("app.api.routes.generate_deck", new=mock.AsyncMock(return_value=ResultPackage())):
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
    routes.job_store._jobs["seeded-job"] = job

    response = client.get("/jobs/seeded-job")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DONE"
    assert body["variants"][0]["pptx_available"] is True
    assert body["variants"][0]["audit_passed"] is True
