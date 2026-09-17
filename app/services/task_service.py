import logging

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies import WorkspaceAccess, WorkspaceTaskAccess
from app.enums import MemberRole, TaskStatus
from app.exceptions import (
    AppException,
    TaskAssignmentConflictError,
    TaskNotFoundError,
    TaskPermissionDeniedError,
    TaskStatusTransitionConflictError,
    WorkspaceMemberNotFoundError,
    WorkspaceNotFoundError,
)
from app.models import Task, WorkspaceMember
from app.policies import (
    can_change_task_assignee,
    can_change_task_status,
    can_delete_task,
    can_edit_task,
    can_self_claim_task,
    can_self_release_task,
    is_valid_status_transition,
)
from app.schemas import (
    TaskAssigneeUpdate,
    TaskCreate,
    TaskListQuery,
    TaskStatusUpdate,
    TaskUpdate,
)


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


def _lock_workspace_memberships(
    db: Session,
    workspace_id: int,
    user_ids: set[int],
) -> dict[int, WorkspaceMember]:
    memberships = db.scalars(
        select(WorkspaceMember)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id.in_(sorted(user_ids)),
        )
        .order_by(WorkspaceMember.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {membership.user_id: membership for membership in memberships}


def _lock_task(
    db: Session,
    workspace_id: int,
    task_id: int,
) -> Task:
    task = db.scalar(
        select(Task)
        .where(
            Task.id == task_id,
            Task.workspace_id == workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise TaskNotFoundError()
    return task


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


def update_task_status(
    db: Session,
    access: WorkspaceTaskAccess,
    data: TaskStatusUpdate,
) -> dict:
    workspace_access = access.workspace_access
    workspace_id = workspace_access.workspace.id
    actor_user_id = workspace_access.current_user.id

    try:
        memberships = _lock_workspace_memberships(
            db,
            workspace_id,
            {actor_user_id},
        )
        actor_membership = memberships.get(actor_user_id)
        if actor_membership is None:
            raise WorkspaceNotFoundError()

        task = _lock_task(db, workspace_id, access.task.id)
        if not can_change_task_status(
            actor_membership.role,
            actor_user_id,
            task.assignee_id,
        ):
            raise TaskPermissionDeniedError()

        if not is_valid_status_transition(
            actor_membership.role,
            task.status,
            data.status,
        ):
            raise TaskStatusTransitionConflictError()

        if task.status == data.status:
            response_data = _task_data(task)
            db.commit()
            return response_data

        task.status = data.status
        db.flush()
        db.refresh(task)
        response_data = _task_data(task)
        db.commit()
    except AppException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise

    logger.info(
        "task_status_updated task_id=%s workspace_id=%s status=%s",
        response_data["id"],
        response_data["workspace_id"],
        response_data["status"],
    )
    return response_data


def update_task_assignee(
    db: Session,
    access: WorkspaceTaskAccess,
    data: TaskAssigneeUpdate,
) -> dict:
    workspace_access = access.workspace_access
    workspace_id = workspace_access.workspace.id
    actor_user_id = workspace_access.current_user.id
    requested_assignee_id = data.assignee_id

    try:
        if (
            workspace_access.membership.role == MemberRole.MEMBER
            and requested_assignee_id not in {None, actor_user_id}
        ):
            raise TaskPermissionDeniedError()

        membership_ids = {actor_user_id}
        if requested_assignee_id is not None:
            membership_ids.add(requested_assignee_id)
        memberships = _lock_workspace_memberships(
            db,
            workspace_id,
            membership_ids,
        )

        actor_membership = memberships.get(actor_user_id)
        if actor_membership is None:
            raise WorkspaceNotFoundError()
        if (
            actor_membership.role == MemberRole.MEMBER
            and requested_assignee_id not in {None, actor_user_id}
        ):
            raise TaskPermissionDeniedError()

        task = _lock_task(db, workspace_id, access.task.id)
        current_assignee_id = task.assignee_id

        if can_change_task_assignee(actor_membership.role):
            if current_assignee_id == requested_assignee_id:
                response_data = _task_data(task)
                db.commit()
                return response_data
            if (
                requested_assignee_id is not None
                and requested_assignee_id not in memberships
            ):
                raise WorkspaceMemberNotFoundError()
            if task.status == TaskStatus.DONE:
                raise TaskAssignmentConflictError()
        else:
            if requested_assignee_id == actor_user_id:
                if current_assignee_id == actor_user_id:
                    response_data = _task_data(task)
                    db.commit()
                    return response_data
                if not can_self_claim_task(
                    actor_membership.role,
                    actor_user_id,
                    current_assignee_id,
                    requested_assignee_id,
                ):
                    raise TaskAssignmentConflictError()
            else:
                if current_assignee_id is None:
                    raise TaskAssignmentConflictError()
                if current_assignee_id != actor_user_id:
                    raise TaskPermissionDeniedError()
                if not can_self_release_task(
                    actor_membership.role,
                    actor_user_id,
                    current_assignee_id,
                    requested_assignee_id,
                ):
                    raise TaskPermissionDeniedError()

            if task.status == TaskStatus.DONE:
                raise TaskAssignmentConflictError()

        task.assignee_id = requested_assignee_id
        db.flush()
        db.refresh(task)
        response_data = _task_data(task)
        db.commit()
    except AppException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise

    logger.info(
        "task_assignee_updated task_id=%s workspace_id=%s assignee_id=%s",
        response_data["id"],
        response_data["workspace_id"],
        response_data["assignee_id"],
    )
    return response_data
