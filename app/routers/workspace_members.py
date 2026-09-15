from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    WorkspaceAccess,
    get_workspace_access,
    require_workspace_roles,
)
from app.enums import MemberRole
from app.schemas import (
    WorkspaceMemberCreate,
    WorkspaceMemberListResponse,
    WorkspaceMemberResponse,
    WorkspaceMemberRoleUpdate,
)
from app.services import workspace_member_service


router = APIRouter(
    prefix="/workspaces/{workspace_id}/members",
    tags=["workspace-members"],
)


@router.get("", response_model=WorkspaceMemberListResponse)
def list_workspace_members(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return workspace_member_service.list_members(
        db=db,
        access=access,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_workspace_member(
    data: WorkspaceMemberCreate,
    access: WorkspaceAccess = Depends(
        require_workspace_roles(MemberRole.OWNER, MemberRole.ADMIN)
    ),
    db: Session = Depends(get_db),
):
    return workspace_member_service.add_member(
        db=db,
        access=access,
        username=data.username,
        role=data.role,
    )


@router.patch(
    "/{user_id}",
    response_model=WorkspaceMemberResponse,
)
def update_workspace_member_role(
    user_id: int,
    data: WorkspaceMemberRoleUpdate,
    access: WorkspaceAccess = Depends(
        require_workspace_roles(MemberRole.OWNER)
    ),
    db: Session = Depends(get_db),
):
    return workspace_member_service.update_member_role(
        db=db,
        access=access,
        user_id=user_id,
        role=data.role,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_workspace_member(
    user_id: int,
    access: WorkspaceAccess = Depends(
        require_workspace_roles(MemberRole.OWNER, MemberRole.ADMIN)
    ),
    db: Session = Depends(get_db),
):
    workspace_member_service.remove_member(
        db=db,
        access=access,
        user_id=user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
