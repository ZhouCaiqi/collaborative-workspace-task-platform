import pytest
from sqlalchemy import Enum as SQLAlchemyEnum
from sqlalchemy import func, inspect, select, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import configure_mappers

from app.enums import MemberRole, TaskStatus
from app.models import Task, User, Workspace, WorkspaceMember


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
    user = User(
        username="task_constraint_owner",
        hashed_password="constraint-test-hash",
    )
    db_session.add(user)
    db_session.flush()
    workspace = Workspace(
        name="Task constraint workspace",
        created_by_id=user.id,
    )
    db_session.add(workspace)
    db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=user.id,
            role=MemberRole.OWNER,
        )
    )
    db_session.commit()

    missing_workspace_id = (
        db_session.scalar(select(func.max(Workspace.id))) or 0
    ) + 1
    missing_user_id = (
        db_session.scalar(select(func.max(User.id))) or 0
    ) + 1
    assert db_session.get(Workspace, missing_workspace_id) is None
    assert db_session.get(User, missing_user_id) is None
    assert db_session.scalar(
        text("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
    ) == 1

    foreign_key_targets = dict(
        db_session.execute(
            text(
                "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME "
                "FROM information_schema.KEY_COLUMN_USAGE "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'tasks' "
                "AND REFERENCED_TABLE_NAME IS NOT NULL"
            )
        ).all()
    )
    assert foreign_key_targets == {
        "workspace_id": "workspaces",
        "creator_id": "users",
        "assignee_id": "users",
    }

    base_values = {
        "title": "Constraint task",
        "priority": 1,
        "workspace_id": workspace.id,
        "creator_id": user.id,
        "assignee_id": None,
        "status": "TODO",
    }
    invalid_overrides = (
        {"workspace_id": missing_workspace_id},
        {"creator_id": missing_user_id},
        {"assignee_id": missing_user_id},
        {"title": "   "},
        {"priority": 0},
        {"status": "INVALID"},
    )
    insert_statement = text(
        "INSERT INTO tasks "
        "(title, priority, workspace_id, creator_id, assignee_id, status) "
        "VALUES (:title, :priority, :workspace_id, :creator_id, "
        ":assignee_id, :status)"
    )

    for overrides in invalid_overrides:
        values = base_values | overrides
        assert db_session.scalar(
            text("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
        ) == 1
        with pytest.raises(DBAPIError):
            db_session.execute(insert_statement, values)
            db_session.commit()
        db_session.rollback()
