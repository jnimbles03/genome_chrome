"""Fixtures for the team API authorization tests.

The suite drives the real Flask app factory (the same ``main:create_app``
object gunicorn serves) and the real psycopg2 storage code. Connection
settings default to a local PostgreSQL database created for these tests.
Override them with the environment variables below if needed.

Defaults (obvious placeholders, not production secrets):

- DB_HOST=127.0.0.1
- DB_PORT=5432
- DB_USER=formgenome
- DB_NAME=formgenome
- DB_PASSWORD=test-only-placeholder
- SECRET_KEY=test-only-placeholder-secret
"""

import os
import sys
from pathlib import Path

# storage.py reads DB_USER, DB_PASSWORD, and DB_NAME at import time, and
# get_db() uses the Cloud SQL unix socket unless DB_HOST is set. Set them
# before importing the application.
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_USER", "formgenome")
os.environ.setdefault("DB_NAME", "formgenome")
os.environ.setdefault("DB_PASSWORD", "test-only-placeholder")
os.environ.setdefault("SECRET_KEY", "test-only-placeholder-secret")
os.environ.setdefault("FLASK_DEBUG", "false")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from main import create_app


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config["TESTING"] = True
    from app.services.storage import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.forms') AS forms_table")
        row = cursor.fetchone()
        if row["forms_table"] is None:
            raise RuntimeError("create_app() did not create the forms table")
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_tables(app):
    from app.services.storage import get_db

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("TRUNCATE TABLE forms, job_queue RESTART IDENTITY")
    yield
