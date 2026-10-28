import threading

import pytest

from rag.api import jobs as jobs_module
from rag.api.jobs import IngestJobs, JobAlreadyRunning
from rag.ingestion.pipeline import IngestReport


def test_a_new_job_is_running():
    jobs = IngestJobs()
    job = jobs.start()
    assert job.status == "running"
    assert job.finished_at is None and job.report is None and job.error is None
    assert jobs.get(job.id) == job


def test_only_one_job_runs_at_a_time():
    jobs = IngestJobs()
    first = jobs.start()
    with pytest.raises(JobAlreadyRunning) as raised:
        jobs.start()
    assert raised.value.job.id == first.id


def test_success_records_the_report_and_frees_the_slot():
    jobs = IngestJobs()
    job = jobs.start()
    report = IngestReport(added=["a.md"], chunks_stored=2)
    jobs.succeed(job.id, report)
    done = jobs.get(job.id)
    assert done.status == "succeeded"
    assert done.report == report
    assert done.finished_at >= done.started_at
    assert jobs.start().id != job.id  # a new job can start


def test_failure_records_the_message_and_frees_the_slot():
    jobs = IngestJobs()
    job = jobs.start()
    jobs.fail(job.id, "Ollama or the database stopped responding.")
    failed = jobs.get(job.id)
    assert failed.status == "failed"
    assert failed.error == "Ollama or the database stopped responding."
    assert failed.report is None and failed.finished_at is not None
    jobs.start()


def test_unknown_job_is_none():
    assert IngestJobs().get("no-such-job") is None


def test_get_returns_a_snapshot():
    jobs = IngestJobs()
    job = jobs.start()
    snapshot = jobs.get(job.id)
    jobs.succeed(job.id, IngestReport())
    assert snapshot.status == "running"  # earlier reads don't change under the caller
    assert jobs.get(job.id).status == "succeeded"


def test_concurrent_starts_create_exactly_one_job():
    jobs = IngestJobs()
    barrier = threading.Barrier(20)
    started, refused = [], []

    def try_start():
        barrier.wait()  # all threads call start() at the same moment
        try:
            started.append(jobs.start())
        except JobAlreadyRunning as running:
            refused.append(running.job.id)

    threads = [threading.Thread(target=try_start) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(started) == 1
    assert refused == [started[0].id] * 19


def test_old_finished_jobs_are_forgotten(monkeypatch):
    monkeypatch.setattr(jobs_module, "MAX_FINISHED_JOBS", 2)
    jobs = IngestJobs()
    ids = []
    for _ in range(4):
        job = jobs.start()
        jobs.succeed(job.id, IngestReport())
        ids.append(job.id)
    running = jobs.start()
    assert [jobs.get(job_id) is not None for job_id in ids] == [False, False, True, True]
    assert jobs.get(running.id).status == "running"  # a running job is never dropped
