"""API-contract tests against a pre-seeded JobStore (no real generation) —
FastAPI TestClient, in-process fallback mode.
"""

from __future__ import annotations

from pathlib import Path

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
from tests.conftest import FIXTURE_TEMPLATE

FIXTURE_PATH = str(FIXTURE_TEMPLATE)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_job_store():
    routes.job_store.clear()
    yield
    routes.job_store.clear()


def _presentation_ir(n: int = 10) -> PresentationIR:
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
            for i in range(n)
        ],
    )


def _seed_done_job(
    job_id: str = "job-1", *, issues: list[AuditIssue] | None = None, revision: int = 1
):
    manifest = TemplateParser().parse(FIXTURE_PATH)
    variant_result = VariantResult(
        variant="A",
        pptx_path="/fake/variant_A.pptx",
        pdf_path="/fake/variant_A.pdf",
        preview_paths=[f"/fake/preview_{i}.png" for i in range(10)],
        audit_report=AuditReport(variant="A", issues=issues or []),
        presentation_ir=_presentation_ir(),
        revision=revision,
    )
    routes.job_store.put(
        Job(
            job_id=job_id,
            status=JobStatus.DONE,
            result=ResultPackage(
                variants={"A": variant_result}, template_manifest=manifest
            ),
            template_filename="template.pptx",
        )
    )
    return variant_result


# --------------------------------------------------------------------------- #
# /result shape
# --------------------------------------------------------------------------- #


def test_result_variant_and_slide_key_sets_are_locked():
    _seed_done_job()

    body = client.get("/jobs/job-1/result").json()
    variant = body["variants"][0]

    assert set(variant.keys()) == {
        "code",
        "name",
        "audience",
        "description",
        "revision",
        "export_state",
        "status",
        "error",
        "audit_score",
        "metrics",
        "slides",
        "exports",
    }
    slide = variant["slides"][0]
    assert set(slide.keys()) == {
        "position",
        "slide_index",
        "title",
        "layout_type",
        "component_types",
        "preview_url",
        "audit_status",
        "issues",
    }


def test_result_slide_positions_are_one_based_and_contiguous():
    _seed_done_job()

    body = client.get("/jobs/job-1/result").json()
    positions = [s["position"] for s in body["variants"][0]["slides"]]

    assert positions == list(range(1, len(positions) + 1))


def test_result_issues_attach_to_the_slide_by_slide_index():
    issue = AuditIssue(
        issue_type=IssueType.OVERFLOW,
        severity=Severity.CRITICAL,
        slide_index=3,
        message="overflow",
    )
    _seed_done_job(issues=[issue])

    body = client.get("/jobs/job-1/result").json()
    slides = body["variants"][0]["slides"]

    assert slides[3]["issues"][0]["issue_type"] == "OVERFLOW"
    assert slides[3]["audit_status"] == "critical"
    for i, slide in enumerate(slides):
        if i != 3:
            assert slide["issues"] == []
            assert slide["audit_status"] == "ok"


@pytest.mark.parametrize(
    ("critical", "warning", "expected_score"),
    [(0, 0, 100), (1, 0, 85), (0, 1, 95), (2, 3, 55), (10, 0, 0), (7, 0, 0)],
)
def test_audit_score_formula(critical, warning, expected_score):
    issues = [
        AuditIssue(
            issue_type=IssueType.OVERFLOW,
            severity=Severity.CRITICAL,
            slide_index=0,
            message="c",
        )
        for _ in range(critical)
    ] + [
        AuditIssue(
            issue_type=IssueType.SEMANTIC_WEAK_TITLE,
            severity=Severity.WARNING,
            slide_index=0,
            message="w",
        )
        for _ in range(warning)
    ]
    _seed_done_job(issues=issues)

    body = client.get("/jobs/job-1/result").json()

    assert body["variants"][0]["audit_score"] == expected_score


def test_metrics_are_within_zero_to_one_hundred():
    _seed_done_job()

    body = client.get("/jobs/job-1/result").json()
    metrics = body["variants"][0]["metrics"]

    for key in ("text_density", "conclusions", "data", "visuals"):
        assert 0 <= metrics[key] <= 100


# --------------------------------------------------------------------------- #
# Routes work under both "/" and "/api"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_health_and_result_work_under_both_prefixes(prefix):
    _seed_done_job()

    health = client.get(f"{prefix}/health")
    result = client.get(f"{prefix}/jobs/job-1/result")

    assert health.status_code == 200
    assert result.status_code == 200


# --------------------------------------------------------------------------- #
# STALE handling
# --------------------------------------------------------------------------- #


def test_stale_variant_files_return_409():
    variant_result = _seed_done_job()
    variant_result.export_state = "STALE"
    job = routes.job_store.get("job-1")
    job.result.variants["A"] = variant_result
    routes.job_store.put(job)

    response = client.get("/jobs/job-1/files/A/pptx")

    assert response.status_code == 409


def test_download_skips_stale_variants():
    variant_result = _seed_done_job()
    variant_result.export_state = "STALE"
    job = routes.job_store.get("job-1")
    job.result.variants["A"] = variant_result
    routes.job_store.put(job)

    response = client.get("/jobs/job-1/download")

    assert response.status_code == 404  # the only variant is STALE and gets skipped


# --------------------------------------------------------------------------- #
# Known bug §12.14: /files and /audit don't uppercase the variant code
# --------------------------------------------------------------------------- #


@pytest.mark.xfail(
    strict=True,
    reason="BUG (ARCHITECTURE.md §12.14): /jobs/{id}/files/{V}/{kind} and "
    "/jobs/{id}/audit/{V} look up job.result.variants.get(variant) verbatim "
    "(routes.py, get_job_file and get_job_audit), unlike /previews, /revise "
    "and /revisions which call variant.upper() first — so a lowercase "
    "variant code 404s here but works for /previews.",
)
def test_lowercase_variant_works_for_files_and_audit_like_previews_does():
    manifest_path = Path("dummy.pptx")
    variant_result = _seed_done_job()
    preview_response = client.get("/jobs/job-1/previews/a/1")
    files_response = client.get("/jobs/job-1/files/a/pptx")
    audit_response = client.get("/jobs/job-1/audit/a")

    assert preview_response.status_code != 404
    assert files_response.status_code != 404
    assert audit_response.status_code != 404
    del manifest_path, variant_result


# --------------------------------------------------------------------------- #
# DELETE
# --------------------------------------------------------------------------- #


def test_delete_removes_storage_dir_and_child_jobs(tmp_cwd):
    # DELETE resolves the storage dir via FastAPI's Depends(get_settings),
    # which reads Settings() fresh (including the real .env) rather than the
    # module-level `routes._settings` singleton, so the storage dir must be
    # located the same way rather than faked through a monkeypatch.
    from app.config import get_settings as real_get_settings

    real_settings = real_get_settings()
    job_dir = Path(real_settings.storage_dir) / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "template.pptx").write_bytes(b"fake")
    _seed_done_job()
    routes.job_store.put(
        Job(
            job_id="child-1",
            status=JobStatus.DONE,
            job_type=JobType.REVISE_SLIDE,
            parent_job_id="job-1",
            variant="A",
            slide_position=1,
        )
    )

    response = client.delete("/jobs/job-1")

    assert response.status_code == 204
    assert routes.job_store.get("job-1") is None
    assert routes.job_store.get("child-1") is None
    assert not job_dir.exists()
