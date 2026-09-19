from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    WorkspaceAccess,
    get_current_user,
    get_rate_limited_current_user,
    get_workspace_access,
)
from app.models import User
from app.schemas import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceResponse,
)
from app.services import workspace_service


router = APIRouter(
    prefix="/workspaces",
    tags=["workspaces"],
)


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    data: WorkspaceCreate,
    current_user: User = Depends(
        get_rate_limited_current_user("workspace.create")
    ),
    db: Session = Depends(get_db),
):
    return workspace_service.create_workspace(
        db=db,
        current_user=current_user,
        name=data.name,
    )


@router.get("", response_model=WorkspaceListResponse)
def list_workspaces(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return workspace_service.list_workspaces(
        db=db,
        current_user=current_user,
        limit=limit,
        offset=offset,
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    access: WorkspaceAccess = Depends(get_workspace_access),
):
    return workspace_service.get_workspace_data(
        workspace=access.workspace,
        current_role=access.membership.role,
    )
