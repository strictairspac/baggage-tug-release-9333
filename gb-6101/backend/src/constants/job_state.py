from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class ArchiveState(StrEnum):
    QUEUED = "queued"
    ARCHIVED = "archived"
    RECONCILED = "reconciled"
    DONE = "done"
    FAILED = "failed"
