from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    WorkspaceAccess,
    WorkspaceTaskAccess,
    get_rate_limited_workspace_access,
    get_rate_limited_workspace_task_access,
    get_workspace_access,
    get_workspace_task_access,
)
from app.schemas import (
    TaskAssigneeUpdate,
    TaskCreate,
    TaskListQuery,
    TaskListResponse,
    TaskResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from app.services import task_service


router = APIRouter(
    prefix="/workspaces/{workspace_id}/tasks",
    tags=["tasks"],
)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    query: Annotated[TaskListQuery, Query()],
    access: WorkspaceAccess = Depends(get_workspace_access),
    db: Session = Depends(get_db),
):
    return task_service.list_tasks(
        db=db,
        access=access,
        query=query,
    )


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    data: TaskCreate,
    access: WorkspaceAccess = Depends(
        get_rate_limited_workspace_access("task.create")
    ),
    db: Session = Depends(get_db),
):
    return task_service.create_task(
        db=db,
        access=access,
        data=data,
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    access: WorkspaceTaskAccess = Depends(get_workspace_task_access),
):
    return task_service.get_task_data(access)


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    data: TaskUpdate,
    access: WorkspaceTaskAccess = Depends(
        get_rate_limited_workspace_task_access("task.update")
    ),
    db: Session = Depends(get_db),
):
    return task_service.update_task(
        db=db,
        access=access,
        data=data,
    )


@router.patch("/{task_id}/status", response_model=TaskResponse)
def update_task_status(
    data: TaskStatusUpdate,
    access: WorkspaceTaskAccess = Depends(
        get_rate_limited_workspace_task_access("task.status")
    ),
    db: Session = Depends(get_db),
):
    return task_service.update_task_status(
        db=db,
        access=access,
        data=data,
    )


@router.patch("/{task_id}/assignee", response_model=TaskResponse)
def update_task_assignee(
    data: TaskAssigneeUpdate,
    access: WorkspaceTaskAccess = Depends(
        get_rate_limited_workspace_task_access("task.assignee")
    ),
    db: Session = Depends(get_db),
):
    return task_service.update_task_assignee(
        db=db,
        access=access,
        data=data,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_task(
    access: WorkspaceTaskAccess = Depends(
        get_rate_limited_workspace_task_access("task.delete")
    ),
    db: Session = Depends(get_db),
):
    task_service.delete_task(db=db, access=access)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
