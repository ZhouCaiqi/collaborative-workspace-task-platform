import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import configure_mappers

from app.enums import TaskStatus
from app.models import Task, User, Workspace


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


def test_database_rejects_invalid_task_checks_and_foreign_keys(db_session):
    # This black-box integration probe isolates the confirmed pytest-cov and
    # MySQL 9.7 SQL-layer FK interaction, including test-schema creation.
    # App coverage stays in this process.
    probe_path = Path(__file__).parent / "support" / "task_constraint_probe.py"
    probe_environment = os.environ.copy()
    for variable_name in tuple(probe_environment):
        if (
            variable_name == "COVERAGE_PROCESS_START"
            or variable_name.startswith("COV_CORE_")
            or variable_name.startswith("COVERAGE_")
        ):
            probe_environment.pop(variable_name)

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
