import pytest
from sqlalchemy import func, select, update

from src.config.resources import engine
from src.constants.job_state import ArchiveState, JobState
from src.models.archives import archive_jobs
from src.models.cases import cases
from src.models.runs import runs
from src.services import archives, execution
from src.utils.serial import content_digest

FIXTURE = "YP-1124-AM"


def case_id_by_code(code):
    with engine.connect() as db:
        return db.scalar(select(cases.c.id).where(cases.c.code == code))


def payload_of(case_id):
    with engine.connect() as db:
        return db.scalar(select(cases.c.payload).where(cases.c.id == case_id))


def job_of(case_id):
    with engine.connect() as db:
        return archives.get(db, case_id)


def run_row(run_id):
    with engine.connect() as db:
        return dict(db.execute(select(runs).where(runs.c.id == run_id)).mappings().one())


def enqueue(client, operator, case_id, parameter=480):
    response = client.post(f"/cases/{case_id}/runs", json={"parameter": parameter}, headers=operator)
    assert response.status_code == 202, response.text
    return response.json()["id"]


def drive_runs():
    while True:
        try:
            if not execution.service_one():
                return
        except execution.JobFailure as exc:
            execution.fail(exc)


def run_case(client, operator, case_id, parameter=480):
    run_id = enqueue(client, operator, case_id, parameter)
    drive_runs()
    return run_id


def drive_archive():
    while archives.service_one():
        pass


def step_archive():
    try:
        return archives.service_one()
    except archives.ArchiveFailure as exc:
        archives.fail(exc)
        return exc


def test_archive_requires_reviewer(client, operator, viewer):
    case_id = case_id_by_code(FIXTURE)
    assert client.post(f"/cases/{case_id}/archive").status_code == 401
    assert client.post(f"/cases/{case_id}/archive", headers=operator).status_code == 403
    assert client.post(f"/cases/{case_id}/archive", headers=viewer).status_code == 403


def test_archive_status_endpoint(client, reviewer, viewer):
    case_id = case_id_by_code(FIXTURE)
    assert client.get(f"/cases/{case_id}/archive", headers=viewer).json() is None
    assert client.get("/cases/99999/archive", headers=viewer).status_code == 404
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    assert client.get(f"/cases/{case_id}/archive", headers=viewer).json()["state"] == "queued"


def test_full_lifecycle(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_id = run_case(client, operator, case_id)
    assert client.post(f"/runs/{run_id}/review", json={"note": "交接核对无误"}, headers=reviewer).status_code == 201
    original = payload_of(case_id)
    digest = content_digest(original)
    before_export = client.get(f"/runs/{run_id}/export", headers=reviewer)
    assert before_export.status_code == 200

    created = client.post(f"/cases/{case_id}/archive", headers=reviewer)
    assert created.status_code == 201
    assert created.json()["state"] == "queued"

    drive_archive()
    job = job_of(case_id)
    assert job["state"] == ArchiveState.DONE
    assert job["digest"] == digest
    assert job["items"] == {"vehicles": 4, "requirements": 3, "evidence": 6}
    assert job["finished_at"] is not None
    assert payload_of(case_id) == {"archived": True}

    archived = archives.read_archive(case_id)
    assert archived == original
    assert content_digest(archived) == digest

    after_export = client.get(f"/runs/{run_id}/export", headers=reviewer)
    assert after_export.status_code == 200
    assert after_export.content == before_export.content
    body = after_export.json()
    assert body["review"]["note"] == "交接核对无误"
    assert body["digest"] == run_row(run_id)["digest"]

    check = client.get(f"/runs/{run_id}/recheck", headers=reviewer)
    assert check.status_code == 200
    assert check.json()["matched"] is True
    assert check.json()["source"] == "archive"
    assert check.json()["recomputed_digest"] == run_row(run_id)["digest"]


def test_initiate_is_idempotent(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    again = client.post(f"/cases/{case_id}/archive", headers=reviewer)
    assert again.status_code == 200
    assert again.json()["state"] == "queued"
    with engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(archive_jobs)) == 1
    drive_archive()
    finished = client.post(f"/cases/{case_id}/archive", headers=reviewer)
    assert finished.status_code == 200
    assert finished.json()["state"] == "done"
    with engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(archive_jobs)) == 1


def test_interrupted_archive_resumes(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201

    assert archives.service_one() is True
    assert job_of(case_id)["state"] == ArchiveState.ARCHIVED
    assert payload_of(case_id)["vehicles"]  # 中断在清理前，原件仍在

    assert archives.service_one() is True
    assert job_of(case_id)["state"] == ArchiveState.RECONCILED
    assert payload_of(case_id)["vehicles"]

    assert archives.service_one() is True
    job = job_of(case_id)
    assert job["state"] == ArchiveState.DONE
    assert payload_of(case_id) == {"archived": True}
    assert archives.service_one() is False


def test_retry_after_corruption_does_not_duplicate(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    assert archives.service_one() is True
    path = archives.archive_path(case_id)
    good = path.read_bytes()

    path.write_bytes(b'{"corrupted": true}')
    failure = step_archive()
    assert isinstance(failure, archives.ArchiveFailure)
    job = job_of(case_id)
    assert job["state"] == ArchiveState.FAILED
    assert "对账不通过" in job["failure"]
    assert payload_of(case_id)["vehicles"]  # 对账未通过，原件保留

    retry = client.post(f"/cases/{case_id}/archive", headers=reviewer)
    assert retry.status_code == 200
    assert retry.json()["state"] == "queued"
    drive_archive()
    job = job_of(case_id)
    assert job["state"] == ArchiveState.DONE
    assert job["attempts"] > 1
    assert path.read_bytes() == good
    with engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(archive_jobs)) == 1


def test_archive_step_skips_rewrite_when_intact(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    assert archives.service_one() is True
    path = archives.archive_path(case_id)
    content = path.read_bytes()
    stamp = path.stat().st_mtime_ns

    with engine.begin() as db:
        db.execute(update(archive_jobs).where(archive_jobs.c.case_id == case_id)
                   .values(state=ArchiveState.QUEUED))
    assert archives.service_one() is True
    assert job_of(case_id)["state"] == ArchiveState.ARCHIVED
    assert path.read_bytes() == content
    assert path.stat().st_mtime_ns == stamp


def test_reconcile_rejects_tampered_originals(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    assert archives.service_one() is True

    tampered = payload_of(case_id)
    tampered["evidence"][0]["expires"] = 999
    with engine.begin() as db:
        db.execute(update(cases).where(cases.c.id == case_id).values(payload=tampered))

    failure = step_archive()
    assert isinstance(failure, archives.ArchiveFailure)
    job = job_of(case_id)
    assert job["state"] == ArchiveState.FAILED
    assert "原件在归档后发生变更" in job["failure"]
    assert payload_of(case_id)["vehicles"]


def test_screening_uninterrupted_during_migration(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    first_run = run_case(client, operator, case_id)
    first_digest = run_row(first_run)["digest"]

    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    assert archives.service_one() is True
    mid_run = enqueue(client, operator, case_id)
    assert archives.service_one() is True
    assert archives.service_one() is True
    assert job_of(case_id)["state"] == ArchiveState.DONE

    while execution.service_one():
        pass
    mid = run_row(mid_run)
    assert mid["status"] == JobState.DONE
    assert mid["digest"] == first_digest

    later_run = run_case(client, operator, case_id)
    assert run_row(later_run)["digest"] == first_digest


def test_recheck_matches_live_and_archived(client, operator, reviewer, viewer):
    case_id = case_id_by_code(FIXTURE)
    run_id = run_case(client, operator, case_id)

    live = client.get(f"/runs/{run_id}/recheck", headers=viewer)
    assert live.status_code == 200
    assert live.json()["matched"] is True
    assert live.json()["source"] == "live"

    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    drive_archive()

    archived = client.get(f"/runs/{run_id}/recheck", headers=viewer)
    assert archived.status_code == 200
    assert archived.json()["matched"] is True
    assert archived.json()["source"] == "archive"
    assert archived.json()["recomputed_digest"] == live.json()["recomputed_digest"]
    assert archived.json()["result"] == live.json()["result"]


def test_recheck_rejects_unfinished_run(client, operator, viewer):
    case_id = case_id_by_code(FIXTURE)
    run_id = enqueue(client, operator, case_id)
    assert client.get(f"/runs/{run_id}/recheck", headers=viewer).status_code == 409


def test_archive_case_without_runs(client, reviewer):
    case_id = case_id_by_code("YP-1124-SV")
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    drive_archive()
    job = job_of(case_id)
    assert job["state"] == ArchiveState.DONE
    assert payload_of(case_id) == {"archived": True}
    assert content_digest(archives.read_archive(case_id)) == job["digest"]


def test_run_fails_cleanly_when_archive_missing(client, operator, reviewer):
    case_id = case_id_by_code(FIXTURE)
    run_case(client, operator, case_id)
    assert client.post(f"/cases/{case_id}/archive", headers=reviewer).status_code == 201
    drive_archive()
    archives.archive_path(case_id).unlink()

    run_id = enqueue(client, operator, case_id)
    drive_runs()
    row = run_row(run_id)
    assert row["status"] == JobState.FAILED
    assert "归档文件缺失" in row["failure"]
