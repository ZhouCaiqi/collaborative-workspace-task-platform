from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.enums import MemberRole, TaskStatus


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=100)
    priority: int = Field(default=1, ge=1, le=5)
    description: str | None = Field(
        default=None,
        max_length=500
    )

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=100
    )
    priority: int | None = Field(default=None, ge=1, le=5)
    description: str | None = Field(
        default=None,
        max_length=500
    )

    @field_validator("title", mode="before")
    @classmethod
    def reject_null_and_strip_title(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def reject_null_priority(cls, value):
        if value is None:
            raise ValueError("Field cannot be null")
        return value

    @model_validator(mode="after")
    def require_update_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one task field must be provided")
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    priority: int
    workspace_id: int
    creator_id: int
    assignee_id: int | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    limit: int
    offset: int


class TaskListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    assignee_id: int | None = Field(default=None, gt=0)
    unassigned: bool = False
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal["id", "priority", "created_at", "updated_at"] = "id"
    sort_order: Literal["asc", "desc"] = "desc"

    @model_validator(mode="after")
    def reject_conflicting_assignee_filters(self):
        if self.assignee_id is not None and self.unassigned:
            raise ValueError(
                "assignee_id cannot be combined with unassigned=true"
            )
        return self


class UserCreate(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_]+$"
    )
    password: str = Field(
        min_length=8,
        max_length=128
    )


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_by_id: int
    created_at: datetime
    updated_at: datetime
    current_role: MemberRole


class WorkspaceListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[WorkspaceResponse]
    total: int
    limit: int
    offset: int


AssignableMemberRole = Literal[MemberRole.ADMIN, MemberRole.MEMBER]


class WorkspaceMemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_]+$",
    )
    role: AssignableMemberRole = MemberRole.MEMBER


class WorkspaceMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: AssignableMemberRole


class WorkspaceMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    username: str
    role: MemberRole
    joined_at: datetime
    updated_at: datetime


class WorkspaceMemberListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[WorkspaceMemberResponse]
    total: int
    limit: int
    offset: int
