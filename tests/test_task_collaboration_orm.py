from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import pymysql
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import make_url
from sqlalchemy.orm import configure_mappers

from app.enums import TaskStatus
from app.models import Task, User, Workspace


MYSQL_PROBE_IMAGE = "mysql:9.7.2"


@contextmanager
def _isolated_constraint_mysql_url():
    suffix = uuid4().hex
    container_name = f"task_test_mysql_constraint_{suffix}"
    try:
        database_name = f"task_constraint_{suffix[:12]}_test_db"
        username = f"probe_{suffix[:12]}"
        password = secrets.token_hex(24)
        root_password = secrets.token_hex(24)

        run_result = subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                container_name,
                "--publish",
                "127.0.0.1::3306",
                "--tmpfs",
                "/var/lib/mysql:rw,nosuid,size=512m",
                "--env",
                f"MYSQL_ROOT_PASSWORD={root_password}",
                "--env",
                f"MYSQL_DATABASE={database_name}",
                "--env",
                f"MYSQL_USER={username}",
                "--env",
                f"MYSQL_PASSWORD={password}",
                MYSQL_PROBE_IMAGE,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if run_result.returncode != 0:
            raise RuntimeError("temporary MySQL container could not be created")

        inspect_result = subprocess.run(
            ["docker", "inspect", container_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if inspect_result.returncode != 0:
            raise RuntimeError("temporary MySQL container could not be inspected")
        container_data = json.loads(inspect_result.stdout)[0]
        port_binding = container_data["NetworkSettings"]["Ports"][
            "3306/tcp"
        ][0]
        if port_binding["HostIp"] != "127.0.0.1":
            raise RuntimeError("temporary MySQL is not loopback-only")
        if container_data["HostConfig"].get("Binds"):
            raise RuntimeError("temporary MySQL unexpectedly has bind mounts")
        if any(
            mount.get("Type") == "volume"
            for mount in container_data.get("Mounts", [])
        ):
            raise RuntimeError("temporary MySQL unexpectedly has a volume")
        if "/var/lib/mysql" not in container_data["HostConfig"].get(
            "Tmpfs",
            {},
        ):
            raise RuntimeError("temporary MySQL data directory is not tmpfs")
        host_port = int(port_binding["HostPort"])

        deadline = time.monotonic() + 90
        while True:
            try:
                connection = pymysql.connect(
                    host="127.0.0.1",
                    port=host_port,
                    user=username,
                    password=password,
                    database=database_name,
                    connect_timeout=1,
                    read_timeout=1,
                    write_timeout=1,
                    autocommit=True,
                )
                break
            except pymysql.MySQLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "temporary MySQL did not become ready"
                    ) from None
                time.sleep(0.2)

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT VERSION(), @@SESSION.FOREIGN_KEY_CHECKS, "
                    "@@GLOBAL.innodb_native_foreign_keys"
                )
                version, foreign_key_checks, native_foreign_keys = cursor.fetchone()
                cursor.execute("SHOW GRANTS FOR CURRENT_USER")
                grants = [row[0] for row in cursor.fetchall()]
        finally:
            connection.close()

        if not str(version).startswith("9.7.2"):
            raise RuntimeError("temporary MySQL version is not 9.7.2")
        if int(foreign_key_checks) != 1:
            raise RuntimeError("temporary MySQL foreign-key checks are disabled")
        if int(native_foreign_keys) != 0:
            raise RuntimeError("temporary MySQL native foreign keys are enabled")

        database_grant_marker = f"on `{database_name}`.*"
        for grant in grants:
            normalized_grant = (
                grant.casefold()
                .replace(r"\_", "_")
                .replace(r"\%", "%")
            )
            if "grant usage on *.*" in normalized_grant:
                continue
            if database_grant_marker not in normalized_grant:
                raise RuntimeError("temporary MySQL user has excessive privileges")

        temporary_url = make_url(
            f"mysql+pymysql://{username}:{password}"
            f"@127.0.0.1:{host_port}/{database_name}"
        )
        development_url = make_url(os.environ["DATABASE_URL"])
        if (
            temporary_url == development_url
            or temporary_url.database == development_url.database
        ):
            raise RuntimeError("temporary MySQL matches the development target")

        yield temporary_url.render_as_string(hide_password=False)
    finally:
        try:
            subprocess.run(
                ["docker", "rm", "--force", container_name],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def test_task_status_is_a_shared_string_enum():
    assert [status.value for status in TaskStatus] == [
        "TODO",
        "IN_PROGRESS",
        "DONE",
    ]
    assert isinstance(TaskStatus.TODO, str)


def test_final_task_columns_constraints_and_indexes():
    table = Task.__table__
    assert set(table.c.keys()) == {
        "id",
        "title",
        "description",
        "priority",
        "workspace_id",
        "creator_id",
        "assignee_id",
        "status",
        "created_at",
        "updated_at",
    }
    for column_name in (
        "title",
        "priority",
        "workspace_id",
        "creator_id",
        "status",
        "created_at",
        "updated_at",
    ):
        assert table.c[column_name].nullable is False
    assert table.c.assignee_id.nullable is True

    assert isinstance(table.c.status.type, SQLAlchemyEnum)
    assert table.c.status.type.native_enum is False
    assert table.c.status.type.enums == ["TODO", "IN_PROGRESS", "DONE"]
    assert table.c.status.server_default.arg == TaskStatus.TODO.value
    assert isinstance(table.c.created_at.type, DATETIME)
    assert table.c.created_at.type.fsp == 6
    assert table.c.created_at.server_default is not None
    assert isinstance(table.c.updated_at.type, DATETIME)
    assert table.c.updated_at.type.fsp == 6
    assert table.c.updated_at.server_default is not None
    assert table.c.updated_at.onupdate is not None

    checks = {constraint.name for constraint in table.constraints}
    assert {
        "ck_tasks_title_length",
        "ck_tasks_priority",
        "ck_tasks_status",
    } <= checks

    foreign_keys = {
        foreign_key.parent.name: foreign_key
        for foreign_key in table.foreign_keys
    }
    assert {
        column_name: (
            foreign_key.column.table.name,
            foreign_key.column.name,
            foreign_key.ondelete,
        )
        for column_name, foreign_key in foreign_keys.items()
    } == {
        "workspace_id": ("workspaces", "id", "RESTRICT"),
        "creator_id": ("users", "id", "RESTRICT"),
        "assignee_id": ("users", "id", "RESTRICT"),
    }

    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert indexes == {
        "ix_tasks_workspace_id_id": ("workspace_id", "id"),
        "ix_tasks_workspace_status_id": (
            "workspace_id",
            "status",
            "id",
        ),
        "ix_tasks_workspace_assignee_id": (
            "workspace_id",
            "assignee_id",
            "id",
        ),
        "ix_tasks_creator_id": ("creator_id",),
        "ix_tasks_assignee_id": ("assignee_id",),
    }


def test_final_task_relationships_have_unambiguous_foreign_keys():
    configure_mappers()

    user_relationships = inspect(User).relationships
    task_relationships = inspect(Task).relationships
    workspace_relationships = inspect(Workspace).relationships

    assert "tasks" not in user_relationships
    assert "owner" not in task_relationships
    assert user_relationships.created_tasks._calculated_foreign_keys == {
        Task.__table__.c.creator_id
    }
    assert user_relationships.assigned_tasks._calculated_foreign_keys == {
        Task.__table__.c.assignee_id
    }
    assert task_relationships.creator._calculated_foreign_keys == {
        Task.__table__.c.creator_id
    }
    assert task_relationships.assignee._calculated_foreign_keys == {
        Task.__table__.c.assignee_id
    }
    assert task_relationships.workspace._calculated_foreign_keys == {
        Task.__table__.c.workspace_id
    }
    assert workspace_relationships.tasks._calculated_foreign_keys == {
        Task.__table__.c.workspace_id
    }


def test_database_rejects_invalid_task_checks_and_foreign_keys():
    # The black-box constraint probe runs without coverage injection against a
    # clean, exclusive MySQL server; application coverage stays in this process.
    probe_path = Path(__file__).parent / "support" / "task_constraint_probe.py"
    probe_environment = os.environ.copy()
    for variable_name in tuple(probe_environment):
        if (
            variable_name == "COVERAGE_PROCESS_START"
            or variable_name.startswith("COV_CORE_")
            or variable_name.startswith("COVERAGE_")
        ):
            probe_environment.pop(variable_name)

    with _isolated_constraint_mysql_url() as temporary_test_database_url:
        probe_environment["TEST_DATABASE_URL"] = temporary_test_database_url
        probe_environment["TEST_DATABASE_RESET_ALLOWED"] = "true"
        result = subprocess.run(
            [
                sys.executable,
                str(probe_path),
            ],
            cwd=Path(__file__).parents[1],
            env=probe_environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    assert result.returncode == 0, (
        f"task constraint probe exited with {result.returncode}: "
        f"stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r}"
    )
    assert result.stdout.strip() == "TASK_CONSTRAINT_PROBE_OK"
    assert result.stderr == ""
