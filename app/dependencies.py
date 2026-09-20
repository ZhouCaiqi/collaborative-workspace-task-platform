from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.enums import MemberRole
from app.exceptions import (
    InvalidTokenError,
    TaskNotFoundError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.models import Task, User, Workspace, WorkspaceMember
from app.rate_limiter import (
    RateLimiter,
    enforce_write_rate_limit,
    get_rate_limiter,
)
from app.security import decode_access_token
from app.services import user_service

import logging


security_logger = logging.getLogger("app.security")


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/users/login", 
    auto_error=False
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    if token is None:
        security_logger.warning(
            "authentication_failed reason=missing_token"
        )
        raise InvalidTokenError
    username = decode_access_token(token)

    if username is None:
        security_logger.warning(
            "authentication_failed reason=invalid_token"
        )
        raise InvalidTokenError()

    user = user_service.get_user_by_username(
        db=db,
        username=username
    )

    if user is None:
        security_logger.warning(
            "authentication_failed reason=user_not_found"
        )
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return user


@dataclass(frozen=True)
class WorkspaceAccess:
    workspace: Workspace
    membership: WorkspaceMember
    current_user: User


@dataclass(frozen=True)
class WorkspaceTaskAccess:
    workspace_access: WorkspaceAccess
    task: Task


def get_workspace_access(
    workspace_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceAccess:
    statement = (
        select(Workspace, WorkspaceMember)
        .join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == Workspace.id,
        )
        .where(
            Workspace.id == workspace_id,
            WorkspaceMember.user_id == current_user.id,
        )
    )
    row = db.execute(statement).one_or_none()

    if row is None:
        raise WorkspaceNotFoundError()

    workspace, membership = row
    return WorkspaceAccess(
        workspace=workspace,
        membership=membership,
        current_user=current_user,
    )


def get_workspace_task_access(
    workspace_id: int,
    task_id: int,
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
) -> WorkspaceTaskAccess:
    task = db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.workspace_id == workspace_id,
        )
    )
    if task is None:
        raise TaskNotFoundError()

    return WorkspaceTaskAccess(
        workspace_access=access,
        task=task,
    )


def require_workspace_roles(
    *allowed_roles: MemberRole,
) -> Callable[..., WorkspaceAccess]:
    def dependency(
        access: WorkspaceAccess = Depends(get_workspace_access),
    ) -> WorkspaceAccess:
        if access.membership.role not in allowed_roles:
            raise WorkspacePermissionDeniedError()
        return access

    return dependency


def get_rate_limited_current_user(
    scope: str,
) -> Callable[..., User]:
    def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> User:
        enforce_write_rate_limit(
            limiter=limiter,
            user_id=current_user.id,
            scope=scope,
            workspace_id=None,
        )
        return current_user

    return dependency


def get_rate_limited_workspace_access(
    scope: str,
) -> Callable[..., WorkspaceAccess]:
    def dependency(
        request: Request,
        access: WorkspaceAccess = Depends(get_workspace_access),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> WorkspaceAccess:
        enforce_write_rate_limit(
            limiter=limiter,
            user_id=access.current_user.id,
            scope=scope,
            workspace_id=access.workspace.id,
        )
        return access

    return dependency


def get_rate_limited_workspace_task_access(
    scope: str,
) -> Callable[..., WorkspaceTaskAccess]:
    def dependency(
        request: Request,
        access: WorkspaceTaskAccess = Depends(get_workspace_task_access),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> WorkspaceTaskAccess:
        workspace_access = access.workspace_access
        enforce_write_rate_limit(
            limiter=limiter,
            user_id=workspace_access.current_user.id,
            scope=scope,
            workspace_id=workspace_access.workspace.id,
        )
        return access

    return dependency


def require_rate_limited_workspace_roles(
    scope: str,
    *allowed_roles: MemberRole,
) -> Callable[..., WorkspaceAccess]:
    role_dependency = require_workspace_roles(*allowed_roles)

    def dependency(
        request: Request,
        access: WorkspaceAccess = Depends(role_dependency),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> WorkspaceAccess:
        enforce_write_rate_limit(
            limiter=limiter,
            user_id=access.current_user.id,
            scope=scope,
            workspace_id=access.workspace.id,
        )
        return access

    return dependency
