"""add task collaboration compatibility structure

Revision ID: 9f4c2a7b1d30
Revises: 6c2f9a4d7e31
Create Date: 2026-09-15 00:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "9f4c2a7b1d30"
down_revision: Union[str, Sequence[str], None] = "6c2f9a4d7e31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable collaboration fields without changing legacy task data."""
    op.add_column(
        "tasks",
        sa.Column("workspace_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("creator_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("assignee_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=True),
    )

    op.create_foreign_key(
        "fk_tasks_workspace_id_workspaces",
        "tasks",
        "workspaces",
        ["workspace_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tasks_creator_id_users",
        "tasks",
        "users",
        ["creator_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_tasks_assignee_id_users",
        "tasks",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_tasks_status",
        "tasks",
        "status IN ('TODO', 'IN_PROGRESS', 'DONE')",
    )

    op.create_index(
        "ix_tasks_workspace_id_id",
        "tasks",
        ["workspace_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_workspace_status_id",
        "tasks",
        ["workspace_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_workspace_assignee_id",
        "tasks",
        ["workspace_id", "assignee_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_creator_id",
        "tasks",
        ["creator_id"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_assignee_id",
        "tasks",
        ["assignee_id"],
        unique=False,
    )

    op.create_table(
        "task_collaboration_user_workspace_map",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("legacy_task_count", sa.Integer(), nullable=False),
        sa.Column("legacy_task_max_id", sa.Integer(), nullable=True),
        sa.Column(
            "migrated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_task_collaboration_map_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_task_collaboration_map_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "user_id",
            name="pk_task_collaboration_user_workspace_map",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            name="uq_task_collaboration_map_workspace_id",
        ),
    )


def downgrade() -> None:
    """Remove compatibility structure after the data revision is reversed."""
    op.drop_table("task_collaboration_user_workspace_map")

    op.drop_constraint(
        "fk_tasks_assignee_id_users",
        "tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_tasks_creator_id_users",
        "tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_tasks_workspace_id_workspaces",
        "tasks",
        type_="foreignkey",
    )
    op.drop_constraint("ck_tasks_status", "tasks", type_="check")

    op.drop_index("ix_tasks_assignee_id", table_name="tasks")
    op.drop_index("ix_tasks_creator_id", table_name="tasks")
    op.drop_index(
        "ix_tasks_workspace_assignee_id",
        table_name="tasks",
    )
    op.drop_index("ix_tasks_workspace_status_id", table_name="tasks")
    op.drop_index("ix_tasks_workspace_id_id", table_name="tasks")

    op.drop_column("tasks", "updated_at")
    op.drop_column("tasks", "created_at")
    op.drop_column("tasks", "status")
    op.drop_column("tasks", "assignee_id")
    op.drop_column("tasks", "creator_id")
    op.drop_column("tasks", "workspace_id")
