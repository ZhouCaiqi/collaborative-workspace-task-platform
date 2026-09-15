from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.dependencies import WorkspaceAccess
from app.enums import MemberRole
from app.exceptions import (
    OwnerMembershipConflictError,
    UserNotFoundError,
    WorkspaceMemberAlreadyExistsError,
    WorkspaceMemberNotFoundError,
    WorkspacePermissionDeniedError,
)
from app.models import User, WorkspaceMember
from app.policies import (
    can_add_member,
    can_remove_member,
    can_update_member_role,
)


def _member_data(
    membership: WorkspaceMember,
    username: str,
) -> dict:
    return {
        "user_id": membership.user_id,
        "username": username,
        "role": membership.role,
        "joined_at": membership.joined_at,
        "updated_at": membership.updated_at,
    }


def _is_duplicate_key_error(exc: IntegrityError) -> bool:
    error_args = getattr(exc.orig, "args", ())
    return bool(error_args) and error_args[0] == 1062


def _get_member_with_username(
    db: Session,
    workspace_id: int,
    user_id: int,
) -> tuple[WorkspaceMember, str]:
    row = db.execute(
        select(WorkspaceMember, User.username)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    ).one_or_none()

    if row is None:
        raise WorkspaceMemberNotFoundError()

    membership, username = row
    return membership, username


def list_members(
    db: Session,
    access: WorkspaceAccess,
    limit: int,
    offset: int,
) -> dict:
    workspace_filter = (
        WorkspaceMember.workspace_id == access.workspace.id
    )
    total = db.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(workspace_filter)
    ) or 0

    rows = db.execute(
        select(WorkspaceMember, User.username)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(workspace_filter)
        .order_by(WorkspaceMember.joined_at, WorkspaceMember.user_id)
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "items": [
            _member_data(membership, username)
            for membership, username in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def add_member(
    db: Session,
    access: WorkspaceAccess,
    username: str,
    role: MemberRole,
) -> dict:
    if not can_add_member(access.membership.role, role):
        raise WorkspacePermissionDeniedError()

    try:
        target_user = db.scalar(
            select(User).where(User.username == username)
        )
        if target_user is None:
            raise UserNotFoundError()

        existing_membership = db.get(
            WorkspaceMember,
            (access.workspace.id, target_user.id),
        )
        if existing_membership is not None:
            raise WorkspaceMemberAlreadyExistsError()

        membership = WorkspaceMember(
            workspace_id=access.workspace.id,
            user_id=target_user.id,
            role=role,
        )
        db.add(membership)
        db.flush()
        db.refresh(membership)
        response_data = _member_data(membership, target_user.username)
        db.commit()
        return response_data
    except IntegrityError as exc:
        db.rollback()
        if _is_duplicate_key_error(exc):
            raise WorkspaceMemberAlreadyExistsError() from exc
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def update_member_role(
    db: Session,
    access: WorkspaceAccess,
    user_id: int,
    role: MemberRole,
) -> dict:
    try:
        membership, username = _get_member_with_username(
            db,
            access.workspace.id,
            user_id,
        )

        if membership.role == MemberRole.OWNER:
            raise OwnerMembershipConflictError()

        if not can_update_member_role(
            access.membership.role,
            membership.role,
            role,
        ):
            raise WorkspacePermissionDeniedError()

        membership.role = role
        db.flush()
        db.refresh(membership)
        response_data = _member_data(membership, username)
        db.commit()
        return response_data
    except SQLAlchemyError:
        db.rollback()
        raise


def remove_member(
    db: Session,
    access: WorkspaceAccess,
    user_id: int,
) -> None:
    try:
        membership, _ = _get_member_with_username(
            db,
            access.workspace.id,
            user_id,
        )

        if membership.role == MemberRole.OWNER:
            if access.membership.role == MemberRole.OWNER:
                raise OwnerMembershipConflictError()
            raise WorkspacePermissionDeniedError()

        if not can_remove_member(
            access.membership.role,
            membership.role,
        ):
            raise WorkspacePermissionDeniedError()

        db.delete(membership)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
