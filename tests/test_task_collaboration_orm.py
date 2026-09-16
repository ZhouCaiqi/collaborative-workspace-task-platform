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


def test_task_compatibility_columns_are_nullable_and_constrained():
    table = Task.__table__

    for column_name in (
        "workspace_id",
        "creator_id",
        "assignee_id",
        "status",
        "created_at",
        "updated_at",
    ):
        assert table.c[column_name].nullable is True

    assert table.c.owner_id.nullable is False
    assert table.c.completed.nullable is False
    assert isinstance(table.c.status.type, SQLAlchemyEnum)
    assert table.c.status.type.native_enum is False
    assert table.c.status.type.enums == ["TODO", "IN_PROGRESS", "DONE"]
    assert isinstance(table.c.created_at.type, DATETIME)
    assert table.c.created_at.type.fsp == 6
    assert isinstance(table.c.updated_at.type, DATETIME)
    assert table.c.updated_at.type.fsp == 6

    checks = {constraint.name for constraint in table.constraints}
    assert "ck_tasks_status" in checks

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


def test_all_user_task_relationships_have_unambiguous_foreign_keys():
    configure_mappers()

    user_relationships = inspect(User).relationships
    task_relationships = inspect(Task).relationships
    workspace_relationships = inspect(Workspace).relationships

    assert user_relationships.tasks._calculated_foreign_keys == {
        Task.__table__.c.owner_id
    }
    assert user_relationships.created_tasks._calculated_foreign_keys == {
        Task.__table__.c.creator_id
    }
    assert user_relationships.assigned_tasks._calculated_foreign_keys == {
        Task.__table__.c.assignee_id
    }
    assert task_relationships.owner._calculated_foreign_keys == {
        Task.__table__.c.owner_id
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
