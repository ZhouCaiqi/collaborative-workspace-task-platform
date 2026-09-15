from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.enums import MemberRole
from app.exceptions import (
    InvalidTokenError,
    WorkspaceNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.models import User, Workspace, WorkspaceMember
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
