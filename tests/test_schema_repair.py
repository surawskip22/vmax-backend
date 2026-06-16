from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap


def run_isolated(script: str, database_url: str, tmp_path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": database_url,
            "DATABASE_SCHEMA": "",
            "STORAGE_PROVIDER": "database",
            "MEDIA_ROOT": str(tmp_path / "media"),
            "WORKER_ENABLED": "false",
            "ADMIN_EMAILS": "",
        }
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_runtime_schema_repair_handles_drifted_head_database(tmp_path):
    db_path = tmp_path / "drifted.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES ('20260616_09')")
    connection.execute("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(320) UNIQUE)")
    connection.execute(
        "INSERT INTO users (id, email) VALUES ('owner-id', 'szef@majster.pl')"
    )
    connection.execute(
        "CREATE TABLE workspaces (id VARCHAR(36) PRIMARY KEY, name VARCHAR(180), kind VARCHAR(30), owner_id VARCHAR(36))"
    )
    connection.execute(
        "INSERT INTO workspaces (id, name, kind, owner_id) VALUES ('workspace-id', 'Firma drift', 'company', 'owner-id')"
    )
    connection.commit()
    connection.close()

    script = """
    import sqlite3
    from sqlalchemy import select

    from panmajster import models
    from panmajster.db import SessionLocal, init_db

    init_db()

    with SessionLocal() as db:
        user = db.scalar(select(models.User).where(models.User.email == "szef@majster.pl"))
        assert user is not None
        assert user.profile_type == "company_owner"
        workspace = db.scalar(
            select(models.Workspace).where(
                models.Workspace.owner_id == user.id,
                models.Workspace.kind == "company",
            )
        )
        assert workspace is not None
        assert hasattr(workspace, "description")

    connection = sqlite3.connect(r"{db_path}")
    user_columns = {{row[1] for row in connection.execute("PRAGMA table_info(users)")}}
    workspace_columns = {{row[1] for row in connection.execute("PRAGMA table_info(workspaces)")}}
    assert "profile_type" in user_columns
    assert "description" in workspace_columns
    connection.close()
    """.format(db_path=str(db_path))

    run_isolated(script, f"sqlite:///{db_path.as_posix()}", tmp_path)


def test_fresh_database_migrates_and_starts_healthcheck(tmp_path):
    db_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "SECRET_KEY": "test-secret",
            "DATABASE_URL": database_url,
            "DATABASE_SCHEMA": "",
            "STORAGE_PROVIDER": "database",
            "MEDIA_ROOT": str(tmp_path / "media"),
            "WORKER_ENABLED": "false",
            "ADMIN_EMAILS": "",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    script = """
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from panmajster import models
    from panmajster.app import create_app
    from panmajster.db import SessionLocal

    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    with SessionLocal() as db:
        assert db.scalar(select(models.User).where(models.User.email == "szef@majster.pl")) is not None
    """

    run_isolated(script, database_url, tmp_path)
