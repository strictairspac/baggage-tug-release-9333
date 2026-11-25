import os
import tempfile

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://workbench:WorkbenchLocal23@127.0.0.1:15432/workbench_test")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("DEMO_PASSWORD", "Demo-DbWorkbench-2026")
os.environ["WORKBENCH_DATA_DIR"] = tempfile.mkdtemp(prefix="workbench-archive-")
os.environ["SEED_DEMO"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from scripts.seed_demo import seed, setup_schema
from src.config.resources import engine
from src.main import app
from src.models.archives import archive_jobs
from src.models.audit import audit
from src.models.cases import cases
from src.models.reviews import reviews
from src.models.runs import runs
from src.models.users import users

TABLES = (reviews, runs, archive_jobs, cases, audit, users)


@pytest.fixture()
def client():
    setup_schema()
    with engine.begin() as db:
        for table in TABLES:
            db.execute(delete(table))
    seed()
    with TestClient(app) as test_client:
        yield test_client


def login(client, username):
    response = client.post("/auth/login", json={"username": username, "password": "Demo-DbWorkbench-2026"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture()
def operator(client):
    return login(client, "operator")


@pytest.fixture()
def reviewer(client):
    return login(client, "reviewer")


@pytest.fixture()
def viewer(client):
    return login(client, "viewer")
