"""create workspace members table

Revision ID: 6c2f9a4d7e31
Revises: 3a1d4e6f8b20
Create Date: 2026-09-15 00:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "6c2f9a4d7e31"
down_revision: Union[str, Sequence[str], None] = "3a1d4e6f8b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workspace membership and its role constraint."""
    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=16),
            server_default="MEMBER",
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("(UTC_TIMESTAMP(6))"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'MEMBER')",
            name="ck_workspace_members_role",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_members_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_members_workspace_id_workspaces",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "user_id",
            name="pk_workspace_members",
        ),
    )
    op.create_index(
        "ix_workspace_members_user_workspace",
        "workspace_members",
        ["user_id", "workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop workspace membership before its parent workspace."""
    op.drop_table("workspace_members")
