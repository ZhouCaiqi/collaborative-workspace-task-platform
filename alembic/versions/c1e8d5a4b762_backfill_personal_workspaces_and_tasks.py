"""backfill personal workspaces and task collaboration fields

Revision ID: c1e8d5a4b762
Revises: 9f4c2a7b1d30
Create Date: 2026-09-15 00:03:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "c1e8d5a4b762"
down_revision: Union[str, Sequence[str], None] = "9f4c2a7b1d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


metadata = sa.MetaData()

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("username", sa.String(length=50), nullable=False),
)

tasks = sa.Table(
    "tasks",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("completed", sa.Boolean(), nullable=False),
    sa.Column("owner_id", sa.Integer(), nullable=False),
    sa.Column("workspace_id", sa.Integer(), nullable=True),
    sa.Column("creator_id", sa.Integer(), nullable=True),
    sa.Column("assignee_id", sa.Integer(), nullable=True),
    sa.Column("status", sa.String(length=16), nullable=True),
    sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=True),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=True),
)

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(length=100), nullable=False),
    sa.Column("created_by_id", sa.Integer(), nullable=False),
    sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
)

workspace_members = sa.Table(
    "workspace_members",
    metadata,
    sa.Column("workspace_id", sa.Integer(), primary_key=True),
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("joined_at", mysql.DATETIME(fsp=6), nullable=False),
    sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
)

workspace_map = sa.Table(
    "task_collaboration_user_workspace_map",
    metadata,
    sa.Column("user_id", sa.Integer(), primary_key=True),
    sa.Column("workspace_id", sa.Integer(), nullable=False),
    sa.Column("legacy_task_count", sa.Integer(), nullable=False),
    sa.Column("legacy_task_max_id", sa.Integer(), nullable=True),
    sa.Column("migrated_at", mysql.DATETIME(fsp=6), nullable=False),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _personal_workspace_name(username: str) -> str:
    return f"Personal Workspace - {username}"


def _count(connection, statement) -> int:
    return int(connection.scalar(statement) or 0)


def _abort(reason: str) -> None:
    raise RuntimeError(
        f"Task collaboration migration validation failed: {reason}"
    )


def _snapshot(connection, table: sa.Table) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            sa.select(table).order_by(*table.primary_key.columns)
        ).all()
    ]


def _validate_upgrade(
    connection,
    original_workspaces: list[tuple],
    original_memberships: list[tuple],
) -> None:
    user_count = _count(connection, sa.select(sa.func.count()).select_from(users))
    mapping_count = _count(
        connection,
        sa.select(sa.func.count()).select_from(workspace_map),
    )
    if mapping_count != user_count:
        _abort("user and mapping counts differ")

    missing_user_mapping_count = _count(
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
    if missing_user_mapping_count:
        _abort("at least one user has no personal workspace mapping")

    mapped_workspace_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.join(
                workspaces,
                workspaces.c.id == workspace_map.c.workspace_id,
            )
        )
        .where(workspaces.c.created_by_id == workspace_map.c.user_id),
    )
    if mapped_workspace_count != mapping_count:
        _abort("a mapped workspace is missing or has the wrong creator")

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
    all_mapped_membership_count = _count(
        connection,
        sa.select(sa.func.count())
        .select_from(
            workspace_map.join(
                workspace_members,
                workspace_members.c.workspace_id
                == workspace_map.c.workspace_id,
            )
        ),
    )
    if (
        matching_owner_count != mapping_count
        or all_mapped_membership_count != mapping_count
    ):
        _abort("a mapped workspace does not have exactly one matching OWNER")

    mappings = connection.execute(
        sa.select(workspace_map).order_by(workspace_map.c.user_id)
    ).mappings().all()
    for mapping in mappings:
        task_count = _count(
            connection,
            sa.select(sa.func.count())
            .select_from(tasks)
            .where(tasks.c.owner_id == mapping["user_id"]),
        )
        task_max_id = connection.scalar(
            sa.select(sa.func.max(tasks.c.id)).where(
                tasks.c.owner_id == mapping["user_id"]
            )
        )
        if (
            task_count != mapping["legacy_task_count"]
            or task_max_id != mapping["legacy_task_max_id"]
        ):
            _abort("legacy task metadata does not match the mapping table")

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
                tasks.c.creator_id != tasks.c.owner_id,
                tasks.c.assignee_id.is_not(None),
                sa.and_(tasks.c.completed.is_(True), tasks.c.status != "DONE"),
                sa.and_(tasks.c.completed.is_(False), tasks.c.status != "TODO"),
            )
        ),
    )
    if invalid_task_count:
        _abort("one or more legacy tasks were backfilled incorrectly")

    mapped_task_count = _count(
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
    total_task_count = _count(
        connection,
        sa.select(sa.func.count()).select_from(tasks),
    )
    if mapped_task_count != total_task_count:
        _abort("at least one legacy task does not use its owner's mapping")

    original_workspace_ids = [row[0] for row in original_workspaces]
    current_original_workspaces = []
    if original_workspace_ids:
        current_original_workspaces = [
            tuple(row)
            for row in connection.execute(
                sa.select(workspaces)
                .where(workspaces.c.id.in_(original_workspace_ids))
                .order_by(workspaces.c.id)
            ).all()
        ]
    if current_original_workspaces != original_workspaces:
        _abort("a pre-existing workspace changed during backfill")

    current_original_memberships = []
    if original_workspace_ids:
        current_original_memberships = [
            tuple(row)
            for row in connection.execute(
                sa.select(workspace_members)
                .where(
                    workspace_members.c.workspace_id.in_(
                        original_workspace_ids
                    )
                )
                .order_by(
                    workspace_members.c.workspace_id,
                    workspace_members.c.user_id,
                )
            ).all()
        ]
    if current_original_memberships != original_memberships:
        _abort("a pre-existing workspace membership changed during backfill")


def upgrade() -> None:
    """Create one mapped personal workspace per user and backfill old tasks."""
    connection = op.get_bind()

    if _count(connection, sa.select(sa.func.count()).select_from(workspace_map)):
        _abort("the mapping table must be empty before upgrade")

    original_workspaces = _snapshot(connection, workspaces)
    original_workspace_ids = [row[0] for row in original_workspaces]
    original_memberships = []
    if original_workspace_ids:
        original_memberships = [
            tuple(row)
            for row in connection.execute(
                sa.select(workspace_members)
                .where(
                    workspace_members.c.workspace_id.in_(
                        original_workspace_ids
                    )
                )
                .order_by(
                    workspace_members.c.workspace_id,
                    workspace_members.c.user_id,
                )
            ).all()
        ]

    migration_time = _utc_now()
    existing_users = connection.execute(
        sa.select(users.c.id, users.c.username).order_by(users.c.id)
    ).mappings().all()

    for user in existing_users:
        user_id = user["id"]
        legacy_task_count = _count(
            connection,
            sa.select(sa.func.count())
            .select_from(tasks)
            .where(tasks.c.owner_id == user_id),
        )
        legacy_task_max_id = connection.scalar(
            sa.select(sa.func.max(tasks.c.id)).where(
                tasks.c.owner_id == user_id
            )
        )

        workspace_result = connection.execute(
            workspaces.insert().values(
                name=_personal_workspace_name(user["username"]),
                created_by_id=user_id,
                created_at=migration_time,
                updated_at=migration_time,
            )
        )
        workspace_id = workspace_result.lastrowid
        if workspace_id is None:
            _abort("the database did not return a personal workspace id")

        connection.execute(
            workspace_members.insert().values(
                workspace_id=workspace_id,
                user_id=user_id,
                role="OWNER",
                joined_at=migration_time,
                updated_at=migration_time,
            )
        )
        connection.execute(
            workspace_map.insert().values(
                user_id=user_id,
                workspace_id=workspace_id,
                legacy_task_count=legacy_task_count,
                legacy_task_max_id=legacy_task_max_id,
                migrated_at=migration_time,
            )
        )
        connection.execute(
            tasks.update()
            .where(tasks.c.owner_id == user_id)
            .values(
                workspace_id=workspace_id,
                creator_id=user_id,
                assignee_id=None,
                status=sa.case(
                    (tasks.c.completed.is_(True), "DONE"),
                    else_="TODO",
                ),
                created_at=migration_time,
                updated_at=migration_time,
            )
        )

    _validate_upgrade(
        connection,
        original_workspaces,
        original_memberships,
    )


def _validate_downgrade(connection) -> list[dict]:
    mappings = [
        dict(row)
        for row in connection.execute(
            sa.select(workspace_map).order_by(workspace_map.c.user_id)
        ).mappings().all()
    ]

    for mapping in mappings:
        user_id = mapping["user_id"]
        workspace_id = mapping["workspace_id"]
        migrated_at = mapping["migrated_at"]

        username = connection.scalar(
            sa.select(users.c.username).where(users.c.id == user_id)
        )
        workspace = connection.execute(
            sa.select(workspaces).where(workspaces.c.id == workspace_id)
        ).mappings().one_or_none()
        if username is None or workspace is None:
            _abort("a mapped user or workspace no longer exists")
        if (
            workspace["name"] != _personal_workspace_name(username)
            or workspace["created_by_id"] != user_id
            or workspace["created_at"] != migrated_at
            or workspace["updated_at"] != migrated_at
        ):
            _abort("a generated personal workspace was modified")

        memberships = connection.execute(
            sa.select(workspace_members).where(
                workspace_members.c.workspace_id == workspace_id
            )
        ).mappings().all()
        if len(memberships) != 1:
            _abort("a generated personal workspace has unexpected members")
        membership = memberships[0]
        if (
            membership["user_id"] != user_id
            or membership["role"] != "OWNER"
            or membership["joined_at"] != migrated_at
            or membership["updated_at"] != migrated_at
        ):
            _abort("a generated OWNER membership was modified")

        owner_task_count = _count(
            connection,
            sa.select(sa.func.count())
            .select_from(tasks)
            .where(tasks.c.owner_id == user_id),
        )
        owner_task_max_id = connection.scalar(
            sa.select(sa.func.max(tasks.c.id)).where(
                tasks.c.owner_id == user_id
            )
        )
        workspace_task_count = _count(
            connection,
            sa.select(sa.func.count())
            .select_from(tasks)
            .where(tasks.c.workspace_id == workspace_id),
        )
        if (
            owner_task_count != mapping["legacy_task_count"]
            or workspace_task_count != mapping["legacy_task_count"]
            or owner_task_max_id != mapping["legacy_task_max_id"]
        ):
            _abort("task counts or identities changed after backfill")

        invalid_task_count = _count(
            connection,
            sa.select(sa.func.count())
            .select_from(tasks)
            .where(
                tasks.c.workspace_id == workspace_id,
                sa.or_(
                    tasks.c.owner_id != user_id,
                    tasks.c.creator_id != tasks.c.owner_id,
                    tasks.c.assignee_id.is_not(None),
                    sa.and_(
                        tasks.c.completed.is_(True),
                        tasks.c.status != "DONE",
                    ),
                    sa.and_(
                        tasks.c.completed.is_(False),
                        tasks.c.status != "TODO",
                    ),
                    tasks.c.created_at != migrated_at,
                    tasks.c.updated_at != migrated_at,
                ),
            ),
        )
        if invalid_task_count:
            _abort("backfilled task data changed after migration")

    return mappings


def downgrade() -> None:
    """Remove only unchanged data created by this migration."""
    connection = op.get_bind()

    # Complete every safety check before the first destructive statement.
    mappings = _validate_downgrade(connection)

    for mapping in mappings:
        connection.execute(
            tasks.update()
            .where(
                tasks.c.owner_id == mapping["user_id"],
                tasks.c.workspace_id == mapping["workspace_id"],
            )
            .values(
                workspace_id=None,
                creator_id=None,
                assignee_id=None,
                status=None,
                created_at=None,
                updated_at=None,
            )
        )

    # The mapping table uses RESTRICT foreign keys, so its rows must be
    # removed before their referenced workspaces. All validated mapping data
    # remains available in memory for the rest of this transaction.
    connection.execute(workspace_map.delete())

    for mapping in mappings:
        connection.execute(
            workspace_members.delete().where(
                workspace_members.c.workspace_id == mapping["workspace_id"],
                workspace_members.c.user_id == mapping["user_id"],
            )
        )
        connection.execute(
            workspaces.delete().where(
                workspaces.c.id == mapping["workspace_id"]
            )
        )
