from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


archive_jobs = Table(
    "archive_jobs", metadata,
    Column("case_id", ForeignKey("cases.id"), primary_key=True),
    Column("state", String(16), nullable=False, index=True),
    Column("digest", String(64), nullable=True),
    Column("items", JSON(none_as_null=True), nullable=True),
    Column("actor", String(48), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("failure", String(320), nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("updated_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
)
