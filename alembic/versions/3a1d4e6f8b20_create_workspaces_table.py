"""create workspaces table

Revision ID: 3a1d4e6f8b20
Revises: 8c5960f0109e
Create Date: 2026-09-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "3a1d4e6f8b20"
down_revision: Union[str, Sequence[str], None] = "8c5960f0109e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the workspace aggregate root."""
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            sa.Integer(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
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
            "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 100",
            name="ck_workspaces_name_length",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_workspaces_created_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
    )
    op.create_index(
        "ix_workspaces_created_by_id",
        "workspaces",
        ["created_by_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the workspace aggregate root."""
    op.drop_table("workspaces")
