from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import TaskCreate, TaskUpdate, TaskResponse, TaskListResponse
from app.services import task_service

from app.dependencies import get_current_user
from app.models import User
from typing import Literal


router = APIRouter(
    prefix="/tasks",
    tags=["tasks"]
)


@router.get("/", response_model=TaskListResponse)
def get_tasks(
    completed: bool | None = None,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    priority: int | None = Query(default=None, ge=1, le=5),
    sort_by: Literal["id", "priority", "title"] = "id",
    sort_order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    items, total = task_service.get_tasks(
        db=db,
        completed=completed,
        limit=limit,
        offset=offset,
        priority=priority,
        sort_by=sort_by,
        sort_order=sort_order,
        owner_id=current_user.id
    )
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset
    }


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = task_service.get_task_by_id(
        db=db,
        task_id=task_id,
        owner_id=current_user.id
    )

    return task


@router.post("/", status_code=201, response_model=TaskResponse)
def create_task(
    task: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return task_service.create_task(
        db=db,
        task=task,
        owner_id=current_user.id
    )


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int, 
    task_update: TaskUpdate, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return task_service.update_task(
        db=db,
        task_id=task_id,
        task_update=task_update,
        owner_id=current_user.id
    )


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return task_service.delete_task(
        db=db,
        task_id=task_id,
        owner_id=current_user.id
    )