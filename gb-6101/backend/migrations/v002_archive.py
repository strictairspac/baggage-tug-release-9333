from src.models import archives  # Register the table.
from src.models.base import metadata


def upgrade(connection):
    # Version 2 adds the archive job ledger; additive and declarative.
    metadata.create_all(connection)
