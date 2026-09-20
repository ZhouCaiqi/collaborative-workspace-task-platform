"""retire task collaboration migration map

Revision ID: e5a1c7d9b302
Revises: d4b6e8f1a203
Create Date: 2026-09-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "e5a1c7d9b302"
down_revision: Union[str, Sequence[str], None] = "d4b6e8f1a203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MAPPING_TABLE = "task_collaboration_user_workspace_map"
EXPECTED_TASK_COLUMNS = {
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
EXPECTED_TASK_INDEXES = {
    "ix_tasks_workspace_id_id": ("workspace_id", "id"),
    "ix_tasks_workspace_status_id": ("workspace_id", "status", "id"),
    "ix_tasks_workspace_assignee_id": (
        "workspace_id",
        "assignee_id",
        "id",
    ),
    "ix_tasks_creator_id": ("creator_id",),
    "ix_tasks_assignee_id": ("assignee_id",),
}
EXPECTED_TASK_FOREIGN_KEYS = {
    "fk_tasks_workspace_id_workspaces": (
        ("workspace_id",),
        "workspaces",
        ("id",),
    ),
    "fk_tasks_creator_id_users": (
        ("creator_id",),
        "users",
        ("id",),
    ),
    "fk_tasks_assignee_id_users": (
        ("assignee_id",),
        "users",
        ("id",),
    ),
}


metadata = sa.MetaData()

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
)

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("created_by_id", sa.Integer(), nullable=False),
)

workspace_members = sa.Table(
    "workspace_members",
    metadata,
    sa.Column("workspace_id", sa.Integer(), primary_key=True),
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("role", sa.String(length=16), nullable=False),
)

tasks = sa.Table(
    "tasks",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("title", sa.String(length=100), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False),
    sa.Column("workspace_id", sa.Integer(), nullable=False),
    sa.Column("creator_id", sa.Integer(), nullable=False),
    sa.Column("assignee_id", sa.Integer(), nullable=True),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
)

workspace_map = sa.Table(
    MAPPING_TABLE,
    metadata,
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("workspace_id", sa.Integer(), nullable=False),
    sa.Column("legacy_task_count", sa.Integer(), nullable=False),
    sa.Column("legacy_task_max_id", sa.Integer(), nullable=True),
    sa.Column("migrated_at", mysql.DATETIME(fsp=6), nullable=False),
)


def _count(connection, statement) -> int:
    return int(connection.scalar(statement) or 0)


def _abort(reason: str) -> None:
    raise RuntimeError(
        "Task collaboration migration-map retirement validation failed: "
        f"{reason}"
    )


def _ondelete(foreign_key: dict) -> str:
    return str(foreign_key.get("options", {}).get("ondelete", "")).upper()


def _validate_mapping_structure(inspector) -> None:
    mapping_columns = {
        column["name"]: column
        for column in inspector.get_columns(MAPPING_TABLE)
    }
    if set(mapping_columns) != {
        "user_id",
        "workspace_id",
        "legacy_task_count",
        "legacy_task_max_id",
        "migrated_at",
    }:
        _abort("migration mapping columns do not match the expected schema")

    for column_name in (
        "user_id",
        "workspace_id",
        "legacy_task_count",
        "migrated_at",
    ):
        if mapping_columns[column_name]["nullable"]:
            _abort("a required migration mapping column is nullable")
    if not mapping_columns["legacy_task_max_id"]["nullable"]:
        _abort("legacy task maximum identifier is unexpectedly required")
    migrated_at_type = mapping_columns["migrated_at"]["type"]
    if (
        not isinstance(migrated_at_type, mysql.DATETIME)
        or migrated_at_type.fsp != 6
    ):
        _abort("migration timestamp precision is not DATETIME(6)")

    primary_key = inspector.get_pk_constraint(MAPPING_TABLE)
    if tuple(primary_key.get("constrained_columns") or ()) != ("user_id",):
        _abort("migration mapping user primary key is missing")

    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(MAPPING_TABLE)
    }
    if unique_constraints.get(
        "uq_task_collaboration_map_workspace_id"
    ) != ("workspace_id",):
        _abort("migration mapping workspace uniqueness is missing")

    foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys(MAPPING_TABLE)
    }
    expected_foreign_keys = {
        "fk_task_collaboration_map_user_id_users": (
            ("user_id",),
            "users",
            ("id",),
        ),
        "fk_task_collaboration_map_workspace_id_workspaces": (
            ("workspace_id",),
            "workspaces",
            ("id",),
        ),
    }
    if set(foreign_keys) != set(expected_foreign_keys):
        _abort("migration mapping foreign keys do not match expectations")
    for name, expected in expected_foreign_keys.items():
        foreign_key = foreign_keys[name]
        actual = (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        if actual != expected or _ondelete(foreign_key) != "RESTRICT":
            _abort("a migration mapping foreign key is invalid")


def _validate_task_structure(inspector) -> None:
    task_columns = {
        column["name"]: column
        for column in inspector.get_columns("tasks")
    }
    if "owner_id" in task_columns or "completed" in task_columns:
        _abort("legacy Task columns still exist")
    if set(task_columns) != EXPECTED_TASK_COLUMNS:
        _abort("Task columns do not match the final schema")

    for column_name in (
        "id",
        "title",
        "priority",
        "workspace_id",
        "creator_id",
        "status",
        "created_at",
        "updated_at",
    ):
        if task_columns[column_name]["nullable"]:
            _abort("a required final Task column is nullable")
    if not task_columns["description"]["nullable"]:
        _abort("Task description is unexpectedly required")
    if not task_columns["assignee_id"]["nullable"]:
        _abort("Task assignee is unexpectedly required")

    status_type = task_columns["status"]["type"]
    if (
        not isinstance(status_type, sa.String)
        or isinstance(status_type, mysql.ENUM)
        or status_type.length != 16
    ):
        _abort("Task status is not the expected constrained VARCHAR")
    for column_name in ("created_at", "updated_at"):
        column_type = task_columns[column_name]["type"]
        if not isinstance(column_type, mysql.DATETIME) or column_type.fsp != 6:
            _abort("a Task timestamp is not DATETIME(6)")

    checks = {
        check["name"]: str(check["sqltext"]).upper()
        for check in inspector.get_check_constraints("tasks")
    }
    if not {
        "ck_tasks_title_length",
        "ck_tasks_priority",
        "ck_tasks_status",
    } <= set(checks):
        _abort("final Task CHECK constraints are incomplete")
    if not all(
        status in checks["ck_tasks_status"]
        for status in ("TODO", "IN_PROGRESS", "DONE")
    ):
        _abort("Task status CHECK does not contain every final status")
    if "TRIM" not in checks["ck_tasks_title_length"]:
        _abort("Task title CHECK does not enforce trimmed length")
    if "BETWEEN 1 AND 5" not in checks["ck_tasks_priority"]:
        _abort("Task priority CHECK does not enforce the final range")

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("tasks")
    }
    if indexes != EXPECTED_TASK_INDEXES:
        _abort("final Task indexes do not match expectations")

    foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("tasks")
    }
    if set(foreign_keys) != set(EXPECTED_TASK_FOREIGN_KEYS):
        _abort("final Task foreign keys do not match expectations")
    for name, expected in EXPECTED_TASK_FOREIGN_KEYS.items():
        foreign_key = foreign_keys[name]
        actual = (
            tuple(foreign_key["constrained_columns"]),
            foreign_key["referred_table"],
            tuple(foreign_key["referred_columns"]),
        )
        if actual != expected or _ondelete(foreign_key) != "RESTRICT":
            _abort("a final Task foreign key is invalid")


def _validate_structure(connection) -> None:
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())
    if MAPPING_TABLE not in table_names:
        _abort("migration mapping table is missing")
    if not {
        "alembic_version",
        "users",
        "workspaces",
        "workspace_members",
        "tasks",
    } <= table_names:
        _abort("a required final application table is missing")

    current_revision = connection.scalar(
        sa.text("SELECT version_num FROM alembic_version")
    )
    if current_revision != down_revision:
        _abort("database revision is not the cleanup predecessor")

    _validate_mapping_structure(inspector)
    _validate_task_structure(inspector)


def _duplicate_value_count(connection, column) -> int:
    duplicates = (
        sa.select(column)
        .select_from(workspace_map)
        .group_by(column)
        .having(sa.func.count() > 1)
        .subquery()
    )
    return _count(
        connection,
        sa.select(sa.func.count()).select_from(duplicates),
    )


def _validate_mapping_data(connection) -> None:
    if _duplicate_value_count(connection, workspace_map.c.user_id):
        _abort("migration mapping contains duplicate users")
    if _duplicate_value_count(connection, workspace_map.c.workspace_id):
        _abort("migration mapping contains duplicate workspaces")

    orphan_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.outerjoin(
                users,
                users.c.id == workspace_map.c.user_id,
            ).outerjoin(
                workspaces,
                workspaces.c.id == workspace_map.c.workspace_id,
            )
        )
        .where(
            sa.or_(
                users.c.id.is_(None),
                workspaces.c.id.is_(None),
            )
        ),
    )
    if orphan_count:
        _abort("migration mapping contains an orphan reference")

    wrong_creator_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.join(
                workspaces,
                workspaces.c.id == workspace_map.c.workspace_id,
            )
        )
        .where(workspaces.c.created_by_id != workspace_map.c.user_id),
    )
    if wrong_creator_count:
        _abort("a mapped workspace has the wrong creator")

    missing_owner_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.outerjoin(
                workspace_members,
                sa.and_(
                    workspace_members.c.workspace_id
                    == workspace_map.c.workspace_id,
                    workspace_members.c.user_id == workspace_map.c.user_id,
                    workspace_members.c.role == "OWNER",
                ),
            )
        )
        .where(workspace_members.c.user_id.is_(None)),
    )
    if missing_owner_count:
        _abort("a mapped workspace is missing its corresponding OWNER")


def _validate_task_data(connection) -> None:
    null_required_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(tasks)
        .where(
            sa.or_(
                tasks.c.id.is_(None),
                tasks.c.title.is_(None),
                tasks.c.priority.is_(None),
                tasks.c.workspace_id.is_(None),
                tasks.c.creator_id.is_(None),
                tasks.c.status.is_(None),
                tasks.c.created_at.is_(None),
                tasks.c.updated_at.is_(None),
            )
        ),
    )
    if null_required_count:
        _abort("a final Task required field is null")

    invalid_status_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(tasks)
        .where(tasks.c.status.not_in(("TODO", "IN_PROGRESS", "DONE"))),
    )
    if invalid_status_count:
        _abort("a Task has an invalid final status")

    creator_users = users.alias("task_creator_users")
    assignee_users = users.alias("task_assignee_users")
    orphan_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            tasks.outerjoin(
                workspaces,
                workspaces.c.id == tasks.c.workspace_id,
            ).outerjoin(
                creator_users,
                creator_users.c.id == tasks.c.creator_id,
            ).outerjoin(
                assignee_users,
                assignee_users.c.id == tasks.c.assignee_id,
            )
        )
        .where(
            sa.or_(
                workspaces.c.id.is_(None),
                creator_users.c.id.is_(None),
                sa.and_(
                    tasks.c.assignee_id.is_not(None),
                    assignee_users.c.id.is_(None),
                ),
            )
        ),
    )
    if orphan_count:
        _abort("a final Task contains an orphan reference")


def _validate_upgrade(connection) -> None:
    # MySQL DDL is not transactional. Every schema and data check must finish
    # successfully before the one destructive statement in this revision.
    _validate_structure(connection)
    _validate_mapping_data(connection)
    _validate_task_data(connection)


def upgrade() -> None:
    """Drop the validated migration-only mapping table."""
    connection = op.get_bind()
    _validate_upgrade(connection)
    op.drop_table(MAPPING_TABLE)


def downgrade() -> None:
    """Reject unsafe reconstruction of historical migration provenance."""
    raise RuntimeError(
        "Revision e5a1c7d9b302 is an irreversible cleanup boundary; "
        "restore a database backup taken before this revision to downgrade "
        "across it"
    )
