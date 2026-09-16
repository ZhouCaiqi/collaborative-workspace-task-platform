from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import MemberRole, TaskStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True
    )

    username: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        index=True,
        nullable=False
    )

    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="owner",
        foreign_keys="Task.owner_id",
    )

    created_tasks: Mapped[list["Task"]] = relationship(
        back_populates="creator",
        foreign_keys="Task.creator_id",
    )

    assigned_tasks: Mapped[list["Task"]] = relationship(
        back_populates="assignee",
        foreign_keys="Task.assignee_id",
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user"
    )

    created_workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="creator",
        foreign_keys="Workspace.created_by_id",
    )

class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('TODO', 'IN_PROGRESS', 'DONE')",
            name="ck_tasks_status",
        ),
        Index("ix_tasks_workspace_id_id", "workspace_id", "id"),
        Index(
            "ix_tasks_workspace_status_id",
            "workspace_id",
            "status",
            "id",
        ),
        Index(
            "ix_tasks_workspace_assignee_id",
            "workspace_id",
            "assignee_id",
            "id",
        ),
        Index("ix_tasks_creator_id", "creator_id"),
        Index("ix_tasks_assignee_id", "assignee_id"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True
    )

    title: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )

    completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    priority: Mapped[int] = mapped_column(
        default=1,
        nullable=False
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )

    owner: Mapped[User] = relationship(
        back_populates="tasks",
        foreign_keys=[owner_id],
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True
    )

    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
    )

    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    status: Mapped[TaskStatus | None] = mapped_column(
        SQLAlchemyEnum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        default=TaskStatus.TODO,
        nullable=True,
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        nullable=True,
    )

    updated_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        onupdate=utc_now,
        nullable=True,
    )

    workspace: Mapped["Workspace | None"] = relationship(
        back_populates="tasks",
        foreign_keys=[workspace_id],
    )

    creator: Mapped[User | None] = relationship(
        back_populates="created_tasks",
        foreign_keys=[creator_id],
    )

    assignee: Mapped[User | None] = relationship(
        back_populates="assigned_tasks",
        foreign_keys=[assignee_id],
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint(
            "CHAR_LENGTH(TRIM(name)) BETWEEN 1 AND 100",
            name="ck_workspaces_name_length",
        ),
        Index("ix_workspaces_created_by_id", "created_by_id"),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
        nullable=False,
    )

    creator: Mapped[User] = relationship(
        back_populates="created_workspaces",
        foreign_keys=[created_by_id],
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace"
    )

    tasks: Mapped[list[Task]] = relationship(
        back_populates="workspace",
        foreign_keys="Task.workspace_id",
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('OWNER', 'ADMIN', 'MEMBER')",
            name="ck_workspace_members_role",
        ),
        Index(
            "ix_workspace_members_user_workspace",
            "user_id",
            "workspace_id",
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        primary_key=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )

    role: Mapped[MemberRole] = mapped_column(
        SQLAlchemyEnum(
            MemberRole,
            name="workspace_member_role",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            length=16,
        ),
        default=MemberRole.MEMBER,
        server_default=MemberRole.MEMBER.value,
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        default=utc_now,
        onupdate=utc_now,
        server_default=text("(UTC_TIMESTAMP(6))"),
        nullable=False,
    )

    workspace: Mapped[Workspace] = relationship(
        back_populates="memberships"
    )

    user: Mapped[User] = relationship(
        back_populates="memberships"
    )
