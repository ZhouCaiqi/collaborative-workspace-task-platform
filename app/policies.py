from app.enums import MemberRole, TaskStatus


def can_add_member(
    actor_role: MemberRole,
    requested_role: MemberRole,
) -> bool:
    if actor_role == MemberRole.OWNER:
        return requested_role in {MemberRole.ADMIN, MemberRole.MEMBER}
    if actor_role == MemberRole.ADMIN:
        return requested_role == MemberRole.MEMBER
    return False


def can_update_member_role(
    actor_role: MemberRole,
    target_role: MemberRole,
    requested_role: MemberRole,
) -> bool:
    return (
        actor_role == MemberRole.OWNER
        and target_role != MemberRole.OWNER
        and requested_role in {MemberRole.ADMIN, MemberRole.MEMBER}
    )


def can_remove_member(
    actor_role: MemberRole,
    target_role: MemberRole,
) -> bool:
    if target_role == MemberRole.OWNER:
        return False
    if actor_role == MemberRole.OWNER:
        return target_role in {MemberRole.ADMIN, MemberRole.MEMBER}
    if actor_role == MemberRole.ADMIN:
        return target_role == MemberRole.MEMBER
    return False


def can_edit_task(
    actor_role: MemberRole,
    actor_user_id: int,
    task_creator_id: int,
) -> bool:
    return (
        actor_role in {MemberRole.OWNER, MemberRole.ADMIN}
        or actor_user_id == task_creator_id
    )


def can_delete_task(
    actor_role: MemberRole,
    actor_user_id: int,
    task_creator_id: int,
) -> bool:
    return can_edit_task(actor_role, actor_user_id, task_creator_id)


def can_change_task_status(
    actor_role: MemberRole,
    actor_user_id: int,
    current_assignee_id: int | None,
) -> bool:
    return (
        actor_role in {MemberRole.OWNER, MemberRole.ADMIN}
        or (
            actor_role == MemberRole.MEMBER
            and current_assignee_id == actor_user_id
        )
    )


def is_valid_status_transition(
    actor_role: MemberRole,
    current_status: TaskStatus,
    requested_status: TaskStatus,
) -> bool:
    if current_status == requested_status:
        return True
    if actor_role in {MemberRole.OWNER, MemberRole.ADMIN}:
        return True
    return (current_status, requested_status) in {
        (TaskStatus.TODO, TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, TaskStatus.DONE),
    }


def can_change_task_assignee(actor_role: MemberRole) -> bool:
    return actor_role in {MemberRole.OWNER, MemberRole.ADMIN}


def can_self_claim_task(
    actor_role: MemberRole,
    actor_user_id: int,
    current_assignee_id: int | None,
    requested_assignee_id: int | None,
) -> bool:
    return (
        actor_role == MemberRole.MEMBER
        and requested_assignee_id == actor_user_id
        and current_assignee_id is None
    )


def can_self_release_task(
    actor_role: MemberRole,
    actor_user_id: int,
    current_assignee_id: int | None,
    requested_assignee_id: int | None,
) -> bool:
    return (
        actor_role == MemberRole.MEMBER
        and requested_assignee_id is None
        and current_assignee_id == actor_user_id
    )
