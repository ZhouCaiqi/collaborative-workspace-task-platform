from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.enums import MemberRole
from app.models import User, Workspace, WorkspaceMember


def _workspace_data(
    workspace: Workspace,
    current_role: MemberRole,
) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "created_by_id": workspace.created_by_id,
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
        "current_role": current_role,
    }


def create_workspace(
    db: Session,
    current_user: User,
    name: str,
) -> dict:
    try:
        workspace = Workspace(
            name=name,
            created_by_id=current_user.id,
        )
        db.add(workspace)
        db.flush()

        owner_membership = WorkspaceMember(
            workspace_id=workspace.id,
            user_id=current_user.id,
            role=MemberRole.OWNER,
        )
        db.add(owner_membership)

        db.flush()
        db.refresh(workspace)
        response_data = _workspace_data(workspace, MemberRole.OWNER)
        db.commit()
        return response_data
    except SQLAlchemyError:
        db.rollback()
        raise


def list_workspaces(
    db: Session,
    current_user: User,
    limit: int,
    offset: int,
) -> dict:
    membership_filter = WorkspaceMember.user_id == current_user.id

    total = db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(membership_filter)
    ) or 0

    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == Workspace.id,
        )
        .where(membership_filter)
        .order_by(Workspace.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "items": [
            _workspace_data(workspace, role)
            for workspace, role in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_workspace_data(
    workspace: Workspace,
    current_role: MemberRole,
) -> dict:
    return _workspace_data(workspace, current_role)
