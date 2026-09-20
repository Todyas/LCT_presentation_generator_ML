from app.pipeline.jobs import JobStatus, JobStore


def test_create_job_defaults_to_pending():
    store = JobStore()

    job = store.create()

    assert store.get(job.job_id).status == JobStatus.PENDING


def test_update_job_reflects_new_status_and_result():
    store = JobStore()
    job = store.create()

    store.update(job.job_id, status=JobStatus.DONE, result={"a": 1})

    updated = store.get(job.job_id)
    assert updated.status == JobStatus.DONE
    assert updated.result == {"a": 1}


def test_get_unknown_id_returns_none():
    store = JobStore()

    assert store.get("does-not-exist") is None
