import logging

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies import WorkspaceAccess, WorkspaceTaskAccess
from app.enums import TaskStatus
from app.exceptions import TaskPermissionDeniedError
from app.models import Task
from app.policies import can_delete_task, can_edit_task
from app.schemas import TaskCreate, TaskListQuery, TaskUpdate


logger = logging.getLogger(__name__)


def _task_data(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "workspace_id": task.workspace_id,
        "creator_id": task.creator_id,
        "assignee_id": task.assignee_id,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def list_tasks(
    db: Session,
    access: WorkspaceAccess,
    query: TaskListQuery,
) -> dict:
    conditions = [Task.workspace_id == access.workspace.id]
    if query.status is not None:
        conditions.append(Task.status == query.status)
    if query.priority is not None:
        conditions.append(Task.priority == query.priority)
    if query.assignee_id is not None:
        conditions.append(Task.assignee_id == query.assignee_id)
    if query.unassigned:
        conditions.append(Task.assignee_id.is_(None))

    total = db.scalar(
        select(func.count()).select_from(Task).where(*conditions)
    ) or 0

    sort_columns = {
        "id": Task.id,
        "priority": Task.priority,
        "created_at": Task.created_at,
        "updated_at": Task.updated_at,
    }
    sort_column = sort_columns[query.sort_by]
    if query.sort_order == "asc":
        order_expressions = [sort_column.asc()]
        if query.sort_by != "id":
            order_expressions.append(Task.id.asc())
    else:
        order_expressions = [sort_column.desc()]
        if query.sort_by != "id":
            order_expressions.append(Task.id.desc())

    tasks = db.scalars(
        select(Task)
        .where(*conditions)
        .order_by(*order_expressions)
        .offset(query.offset)
        .limit(query.limit)
    ).all()

    return {
        "items": [_task_data(task) for task in tasks],
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }


def get_task_data(access: WorkspaceTaskAccess) -> dict:
    return _task_data(access.task)


def create_task(
    db: Session,
    access: WorkspaceAccess,
    data: TaskCreate,
) -> dict:
    task = Task(
        title=data.title,
        description=data.description,
        priority=data.priority,
        workspace_id=access.workspace.id,
        creator_id=access.current_user.id,
        assignee_id=None,
        status=TaskStatus.TODO,
    )

    try:
        db.add(task)
        db.flush()
        db.refresh(task)
        response_data = _task_data(task)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    logger.info(
        "task_created task_id=%s workspace_id=%s creator_id=%s",
        response_data["id"],
        response_data["workspace_id"],
        response_data["creator_id"],
    )
    return response_data


def update_task(
    db: Session,
    access: WorkspaceTaskAccess,
    data: TaskUpdate,
) -> dict:
    workspace_access = access.workspace_access
    task = access.task
    if not can_edit_task(
        workspace_access.membership.role,
        workspace_access.current_user.id,
        task.creator_id,
    ):
        raise TaskPermissionDeniedError()

    update_data = data.model_dump(exclude_unset=True)
    if "title" in update_data:
        task.title = update_data["title"]
    if "description" in update_data:
        task.description = update_data["description"]
    if "priority" in update_data:
        task.priority = update_data["priority"]

    try:
        db.flush()
        db.refresh(task)
        response_data = _task_data(task)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    logger.info(
        "task_updated task_id=%s workspace_id=%s fields=%s",
        response_data["id"],
        response_data["workspace_id"],
        list(update_data),
    )
    return response_data


def delete_task(
    db: Session,
    access: WorkspaceTaskAccess,
) -> None:
    workspace_access = access.workspace_access
    task = access.task
    if not can_delete_task(
        workspace_access.membership.role,
        workspace_access.current_user.id,
        task.creator_id,
    ):
        raise TaskPermissionDeniedError()

    task_id = task.id
    workspace_id = task.workspace_id
    try:
        db.delete(task)
        db.flush()
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    logger.info(
        "task_deleted task_id=%s workspace_id=%s",
        task_id,
        workspace_id,
    )
