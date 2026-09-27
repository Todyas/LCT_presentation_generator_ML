from app.models.presentation_ir import SlideIR, TitleComponent
from app.models.template_manifest import LayoutType
from app.pipeline.jobs import JobStatus, JobStore, SlideRevisionStore
from app.pipeline.orchestrator import ResultPackage


def test_create_job_defaults_to_pending(tmp_path):
    store = JobStore(f"sqlite+pysqlite:///{tmp_path}/jobs.db")

    job = store.create()

    assert store.get(job.job_id).status == JobStatus.PENDING


def test_update_job_reflects_new_status_and_result(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path}/jobs.db"
    store = JobStore(database_url)
    job = store.create()
    result = ResultPackage()

    store.update(job.job_id, status=JobStatus.DONE, result=result)

    updated = JobStore(database_url).get(job.job_id)
    assert updated.status == JobStatus.DONE
    assert updated.result.variants == {}


def test_get_unknown_id_returns_none(tmp_path):
    store = JobStore(f"sqlite+pysqlite:///{tmp_path}/jobs.db")

    assert store.get("does-not-exist") is None


def test_revision_save_is_idempotent_for_source_job(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path}/jobs.db"
    jobs = JobStore(database_url)
    revisions = SlideRevisionStore(database_url)
    deck = jobs.create()
    source = jobs.create(parent_job_id=deck.job_id)
    slide = SlideIR(
        slide_index=0,
        layout_type=LayoutType.CONTENT_1COL,
        title=TitleComponent(text="Вывод"),
        components=[],
    )

    first = revisions.save(
        deck_job_id=deck.job_id,
        variant="A",
        slide_position=1,
        revision_number=2,
        source_job_id=source.job_id,
        correction_prompt="Сократи",
        slide=slide,
        preview_path=None,
        audit_json=None,
    )
    second = revisions.save(
        deck_job_id=deck.job_id,
        variant="A",
        slide_position=1,
        revision_number=2,
        source_job_id=source.job_id,
        correction_prompt="Сократи",
        slide=slide,
        preview_path=None,
        audit_json=None,
    )

    assert second.revision_id == first.revision_id
    assert len(revisions.list(deck.job_id, "A", 1)) == 1
