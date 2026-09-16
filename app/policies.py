from app.enums import MemberRole


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
