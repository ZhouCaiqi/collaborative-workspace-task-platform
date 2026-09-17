import pytest

from app.enums import MemberRole, TaskStatus
from app.policies import (
    can_change_task_assignee,
    can_change_task_status,
    can_self_claim_task,
    can_self_release_task,
    is_valid_status_transition,
)


@pytest.mark.parametrize("role", [MemberRole.OWNER, MemberRole.ADMIN])
def test_managers_can_change_status_and_assignee(role):
    assert can_change_task_status(role, 1, None)
    assert can_change_task_assignee(role)
    assert is_valid_status_transition(
        role,
        TaskStatus.DONE,
        TaskStatus.TODO,
    )


def test_member_status_policy_requires_assignment_and_forward_transition():
    assert can_change_task_status(MemberRole.MEMBER, 1, 1)
    assert not can_change_task_status(MemberRole.MEMBER, 1, 2)
    assert is_valid_status_transition(
        MemberRole.MEMBER,
        TaskStatus.TODO,
        TaskStatus.IN_PROGRESS,
    )
    assert is_valid_status_transition(
        MemberRole.MEMBER,
        TaskStatus.IN_PROGRESS,
        TaskStatus.DONE,
    )
    assert not is_valid_status_transition(
        MemberRole.MEMBER,
        TaskStatus.TODO,
        TaskStatus.DONE,
    )
    assert not is_valid_status_transition(
        MemberRole.MEMBER,
        TaskStatus.DONE,
        TaskStatus.TODO,
    )


def test_member_self_assignment_policies_cover_claim_and_release():
    assert not can_change_task_assignee(MemberRole.MEMBER)
    assert can_self_claim_task(MemberRole.MEMBER, 1, None, 1)
    assert not can_self_claim_task(MemberRole.MEMBER, 1, 2, 1)
    assert not can_self_claim_task(MemberRole.MEMBER, 1, None, 2)
    assert can_self_release_task(MemberRole.MEMBER, 1, 1, None)
    assert not can_self_release_task(MemberRole.MEMBER, 1, 2, None)
    assert not can_self_release_task(MemberRole.MEMBER, 1, 1, 1)
