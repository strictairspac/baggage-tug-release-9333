import json
import logging
import os
from pathlib import Path
from time import time

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from src.config.resources import engine
from src.config.settings import DATA_DIR
from src.constants.job_state import ArchiveState, JobState
from src.domain import scenario
from src.models.archives import archive_jobs
from src.models.cases import cases
from src.models.runs import runs
from src.utils.serial import canonical_json, content_digest
from . import audit
from . import cases as case_service

logger = logging.getLogger(__name__)

ACTIVE_STATES = (ArchiveState.QUEUED, ArchiveState.ARCHIVED, ArchiveState.RECONCILED)
TOMBSTONE = {"archived": True}


def get(db, case_id):
    row = db.execute(select(archive_jobs).where(archive_jobs.c.case_id == case_id)).mappings().first()
    return dict(row) if row else None


def initiate(db, case_id, actor):
    case_service.get(db, case_id)
    existing = get(db, case_id)
    now = int(time())
    if existing is None:
        try:
            with db.begin_nested():
                db.execute(insert(archive_jobs).values(
                    case_id=case_id, state=ArchiveState.QUEUED, actor=actor, attempts=0,
                    created_at=now, updated_at=now,
                ))
        except IntegrityError:
            # 并发发起同一批次的归档，归档任务保持唯一。
            return get(db, case_id), False
        audit.record(db, "archive", actor, "case", case_id)
        return get(db, case_id), True
    if existing["state"] == ArchiveState.FAILED:
        db.execute(update(archive_jobs).where(archive_jobs.c.case_id == case_id).values(
            state=ArchiveState.QUEUED, failure=None, actor=actor, updated_at=now,
        ))
        audit.record(db, "archive", actor, "case", case_id)
        return get(db, case_id), False
    return existing, False


def is_tombstone(payload):
    return isinstance(payload, dict) and payload.get("archived") is True


def archive_path(case_id):
    directory = Path(DATA_DIR) / "archive"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"case-{case_id}.json"


def write_archive(case_id, payload):
    target = archive_path(case_id)
    staging = target.with_suffix(".tmp")
    with open(staging, "wb") as handle:
        handle.write(canonical_json(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staging, target)


def read_archive(case_id):
    path = archive_path(case_id)
    if not path.exists():
        raise ValueError("归档文件缺失，无法核对原件")
    try:
        return json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("归档文件损坏，无法核对原件") from exc


def item_counts(payload):
    return {key: len(payload.get(key) or []) for key in ("vehicles", "requirements", "evidence")}


def resolve_payload(db, case_id):
    payload = db.scalar(select(cases.c.payload).where(cases.c.id == case_id))
    if payload is None:
        raise ValueError("资料不存在")
    if not is_tombstone(payload):
        return payload
    job = get(db, case_id)
    if job is None or job["state"] != ArchiveState.DONE or not job["digest"]:
        raise ValueError("资料原件已清理，但归档记录不完整")
    archived = read_archive(case_id)
    if content_digest(archived) != job["digest"]:
        raise ValueError("归档文件校验失败，摘要与归档记录不一致")
    return archived


def _live_payload(db, case_id):
    payload = db.scalar(select(cases.c.payload).where(cases.c.id == case_id))
    if payload is None:
        raise ValueError("资料不存在")
    return payload


def _touch(db, case_id, **values):
    db.execute(update(archive_jobs).where(archive_jobs.c.case_id == case_id).values(
        **values, updated_at=int(time()),
    ))


def do_archive(db, job):
    case_id = job["case_id"]
    payload = _live_payload(db, case_id)
    if is_tombstone(payload):
        raise ValueError("资料原件已清理，无法重复归档")
    digest = content_digest(payload)
    try:
        archived = read_archive(case_id)
    except ValueError:
        archived = None
    if archived is None or content_digest(archived) != digest:
        write_archive(case_id, payload)
    _touch(db, case_id, state=ArchiveState.ARCHIVED, digest=digest,
           items=item_counts(payload), attempts=job["attempts"] + 1, failure=None)


def do_reconcile(db, job):
    case_id = job["case_id"]
    payload = _live_payload(db, case_id)
    if is_tombstone(payload):
        raise ValueError("原件已清理，归档对账无法执行")
    if content_digest(payload) != job["digest"]:
        raise ValueError("原件在归档后发生变更，对账不通过")
    archived = read_archive(case_id)
    if content_digest(archived) != job["digest"]:
        raise ValueError("归档文件与原件摘要不一致，对账不通过")
    if item_counts(archived) != job["items"]:
        raise ValueError("归档条目数与原件不一致，对账不通过")
    history = db.execute(
        select(runs).where(runs.c.case_id == case_id, runs.c.status == JobState.DONE)
    ).mappings()
    for run in history:
        recomputed = scenario.run(archived, run["parameter"], db)
        if content_digest(recomputed) != run["digest"]:
            raise ValueError(f"按原复核时点复算与批次 #{run['id']} 的历史摘要不一致，对账不通过")
    _touch(db, case_id, state=ArchiveState.RECONCILED, attempts=job["attempts"] + 1)


def do_cleanup(db, job):
    case_id = job["case_id"]
    row = db.execute(
        select(cases.c.payload).where(cases.c.id == case_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ValueError("资料不存在")
    archived = read_archive(case_id)
    if content_digest(archived) != job["digest"]:
        raise ValueError("归档文件校验失败，取消清理原件")
    if not is_tombstone(row):
        if content_digest(row) != job["digest"]:
            raise ValueError("原件在归档后发生变更，取消清理")
        db.execute(update(cases).where(cases.c.id == case_id).values(payload=TOMBSTONE))
    _touch(db, case_id, state=ArchiveState.DONE, attempts=job["attempts"] + 1,
           finished_at=int(time()))
    audit.record(db, "archive_done", "worker", "case", case_id)


STEPS = {
    ArchiveState.QUEUED: do_archive,
    ArchiveState.ARCHIVED: do_reconcile,
    ArchiveState.RECONCILED: do_cleanup,
}


def service_one():
    with engine.begin() as db:
        statement = (
            select(archive_jobs).where(archive_jobs.c.state.in_(ACTIVE_STATES))
            .order_by(archive_jobs.c.case_id).limit(1).with_for_update(skip_locked=True)
        )
        job = db.execute(statement).mappings().first()
        if job is None:
            return False
        try:
            STEPS[job["state"]](db, dict(job))
        except Exception as exc:
            # Let the failed database transaction roll back before persisting the error.
            raise ArchiveFailure(job["case_id"], exc) from exc
        return True


class ArchiveFailure(Exception):
    def __init__(self, case_id, cause):
        super().__init__(str(cause))
        self.case_id = case_id
        self.cause = cause


def fail(archive_failure):
    with engine.begin() as db:
        case_id = archive_failure.case_id
        statement = select(archive_jobs.c.state).where(archive_jobs.c.case_id == case_id).with_for_update()
        if db.scalar(statement) not in ACTIVE_STATES:
            return
        cause = archive_failure.cause
        if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
            reason = str(cause) or "归档资料不符合要求"
        else:
            reason = "归档处理失败，请检查 worker 日志"
        _touch(db, case_id, state=ArchiveState.FAILED, failure=reason[:320])
        audit.record(db, "archive_failed", "worker", "case", case_id)
