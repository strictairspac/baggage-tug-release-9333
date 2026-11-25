from pydantic import BaseModel

from src.constants.job_state import ArchiveState


class ArchiveEntry(BaseModel):
    case_id: int
    state: ArchiveState
    digest: str | None
    items: dict | None
    actor: str
    attempts: int
    failure: str | None
    created_at: int
    updated_at: int
    finished_at: int | None
