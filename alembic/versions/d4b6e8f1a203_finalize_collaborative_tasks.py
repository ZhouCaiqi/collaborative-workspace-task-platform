"""finalize collaborative tasks

Revision ID: d4b6e8f1a203
Revises: c1e8d5a4b762
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "d4b6e8f1a203"
down_revision: Union[str, Sequence[str], None] = "c1e8d5a4b762"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
)

tasks = sa.Table(
    "tasks",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("title", sa.String(length=100), nullable=False),
    sa.Column("completed", sa.Boolean(), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False),
    sa.Column("owner_id", sa.Integer(), nullable=False),
    sa.Column("workspace_id", sa.Integer(), nullable=True),
    sa.Column("creator_id", sa.Integer(), nullable=True),
    sa.Column("assignee_id", sa.Integer(), nullable=True),
    sa.Column("status", sa.String(length=16), nullable=True),
    sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=True),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=True),
)

workspace_members = sa.Table(
    "workspace_members",
    metadata,
    sa.Column("workspace_id", sa.Integer(), primary_key=True),
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("role", sa.String(length=16), nullable=False),
)

workspace_map = sa.Table(
    "task_collaboration_user_workspace_map",
    metadata,
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("workspace_id", sa.Integer(), nullable=False),
)


def _count(connection, statement) -> int:
    return int(connection.scalar(statement) or 0)


def _abort(reason: str) -> None:
    raise RuntimeError(
        f"Final task migration validation failed: {reason}"
    )


def _owner_foreign_key_name(connection) -> str:
    matching_foreign_keys = [
        foreign_key
        for foreign_key in sa.inspect(connection).get_foreign_keys("tasks")
        if foreign_key["constrained_columns"] == ["owner_id"]
        and foreign_key["referred_table"] == "users"
        and foreign_key["referred_columns"] == ["id"]
    ]
    if len(matching_foreign_keys) != 1:
        _abort("expected exactly one owner_id foreign key")
    foreign_key_name = matching_foreign_keys[0]["name"]
    if not foreign_key_name:
        _abort("owner_id foreign key has no database name")
    return foreign_key_name


def _validate_upgrade(connection) -> str:
    user_count = _count(
        connection,
        sa.select(sa.func.count()).select_from(users),
    )
    mapping_count = _count(
        connection,
        sa.select(sa.func.count()).select_from(workspace_map),
    )
    if mapping_count != user_count:
        _abort("user and mapping counts differ")

    missing_mapping_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            users.outerjoin(
                workspace_map,
                workspace_map.c.user_id == users.c.id,
            )
        )
        .where(workspace_map.c.user_id.is_(None)),
    )
    if missing_mapping_count:
        _abort("at least one user has no migration mapping")

    matching_owner_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.join(
                workspace_members,
                sa.and_(
                    workspace_members.c.workspace_id
                    == workspace_map.c.workspace_id,
                    workspace_members.c.user_id == workspace_map.c.user_id,
                ),
            )
        )
        .where(workspace_members.c.role == "OWNER"),
    )
    if matching_owner_count != mapping_count:
        _abort("a mapped workspace is missing its corresponding OWNER")

    invalid_task_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(tasks)
        .where(
            sa.or_(
                tasks.c.workspace_id.is_(None),
                tasks.c.creator_id.is_(None),
                tasks.c.status.is_(None),
                tasks.c.created_at.is_(None),
                tasks.c.updated_at.is_(None),
                tasks.c.status.not_in(("TODO", "IN_PROGRESS", "DONE")),
                tasks.c.creator_id != tasks.c.owner_id,
                sa.and_(tasks.c.completed.is_(True), tasks.c.status != "DONE"),
                sa.and_(tasks.c.completed.is_(False), tasks.c.status != "TODO"),
                ~sa.func.char_length(sa.func.trim(tasks.c.title)).between(
                    1,
                    100,
                ),
                ~tasks.c.priority.between(1, 5),
            )
        ),
    )
    if invalid_task_count:
        _abort("legacy task data does not satisfy final constraints")

    orphan_reference_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            tasks.outerjoin(
                workspaces,
                workspaces.c.id == tasks.c.workspace_id,
            ).outerjoin(
                users,
                users.c.id == tasks.c.creator_id,
            )
        )
        .where(
            sa.or_(
                workspaces.c.id.is_(None),
                users.c.id.is_(None),
            )
        ),
    )
    if orphan_reference_count:
        _abort("a task references a missing workspace or creator")

    total_task_count = _count(
        connection,
        sa.select(sa.func.count()).select_from(tasks),
    )
    correctly_mapped_task_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            tasks.join(
                workspace_map,
                sa.and_(
                    workspace_map.c.user_id == tasks.c.owner_id,
                    workspace_map.c.workspace_id == tasks.c.workspace_id,
                ),
            )
        ),
    )
    if correctly_mapped_task_count != total_task_count:
        _abort("a task does not use its owner's mapped workspace")

    return _owner_foreign_key_name(connection)


def upgrade() -> None:
    """Enforce the final Task schema after validating every legacy row."""
    connection = op.get_bind()

    # All validation, including dynamic FK-name resolution, precedes DDL.
    owner_foreign_key_name = _validate_upgrade(connection)

    op.alter_column(
        "tasks",
        "workspace_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "tasks",
        "creator_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "tasks",
        "status",
        existing_type=sa.String(length=16),
        existing_nullable=True,
        nullable=False,
        server_default="TODO",
    )
    op.alter_column(
        "tasks",
        "created_at",
        existing_type=mysql.DATETIME(fsp=6),
        existing_nullable=True,
        nullable=False,
        server_default=sa.text("(UTC_TIMESTAMP(6))"),
    )
    op.alter_column(
        "tasks",
        "updated_at",
        existing_type=mysql.DATETIME(fsp=6),
        existing_nullable=True,
        nullable=False,
        server_default=sa.text("(UTC_TIMESTAMP(6))"),
    )

    op.create_check_constraint(
        "ck_tasks_title_length",
        "tasks",
        "CHAR_LENGTH(TRIM(title)) BETWEEN 1 AND 100",
    )
    op.create_check_constraint(
        "ck_tasks_priority",
        "tasks",
        "priority BETWEEN 1 AND 5",
    )

    op.drop_constraint(
        owner_foreign_key_name,
        "tasks",
        type_="foreignkey",
    )
    op.drop_column("tasks", "owner_id")
    op.drop_column("tasks", "completed")


def _validate_downgrade(connection) -> None:
    invalid_task_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(tasks)
        .where(
            sa.or_(
                tasks.c.creator_id.is_(None),
                tasks.c.status.is_(None),
                tasks.c.status.not_in(("TODO", "IN_PROGRESS", "DONE")),
            )
        ),
    )
    if invalid_task_count:
        _abort("final task data cannot be converted to legacy fields")


def downgrade() -> None:
    """Restore 2B fields; IN_PROGRESS is lossily mapped to false."""
    connection = op.get_bind()

    # Validate before the first DDL statement. MySQL DDL is not transactional.
    _validate_downgrade(connection)

    op.add_column(
        "tasks",
        sa.Column("owner_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("completed", sa.Boolean(), nullable=True),
    )

    downgraded_tasks = sa.table(
        "tasks",
        sa.column("creator_id", sa.Integer()),
        sa.column("status", sa.String(length=16)),
        sa.column("owner_id", sa.Integer()),
        sa.column("completed", sa.Boolean()),
    )
    connection.execute(
        downgraded_tasks.update().values(
            owner_id=downgraded_tasks.c.creator_id,
            completed=sa.case(
                (downgraded_tasks.c.status == "DONE", True),
                else_=False,
            ),
        )
    )

    null_legacy_field_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(downgraded_tasks)
        .where(
            sa.or_(
                downgraded_tasks.c.owner_id.is_(None),
                downgraded_tasks.c.completed.is_(None),
            )
        ),
    )
    if null_legacy_field_count:
        _abort("legacy task fields were not backfilled completely")

    op.alter_column(
        "tasks",
        "owner_id",
        existing_type=sa.Integer(),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "tasks",
        "completed",
        existing_type=sa.Boolean(),
        existing_nullable=True,
        nullable=False,
    )
    op.create_foreign_key(
        "fk_tasks_owner_id_users",
        "tasks",
        "users",
        ["owner_id"],
        ["id"],
    )

    op.drop_constraint("ck_tasks_priority", "tasks", type_="check")
    op.drop_constraint("ck_tasks_title_length", "tasks", type_="check")

    op.alter_column(
        "tasks",
        "updated_at",
        existing_type=mysql.DATETIME(fsp=6),
        existing_nullable=False,
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "tasks",
        "created_at",
        existing_type=mysql.DATETIME(fsp=6),
        existing_nullable=False,
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "tasks",
        "status",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        nullable=True,
        server_default=None,
    )
    op.alter_column(
        "tasks",
        "creator_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
        nullable=True,
    )
    op.alter_column(
        "tasks",
        "workspace_id",
        existing_type=sa.Integer(),
        existing_nullable=False,
        nullable=True,
    )
