from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies import WorkspaceAccess, WorkspaceTaskAccess
from app.enums import TaskStatus
from app.exceptions import AppException
from app.models import Task, User, Workspace, WorkspaceMember
from app.schemas import TaskAssigneeUpdate, TaskStatusUpdate
from app.services import task_service, workspace_member_service
from conftest import TestingSessionLocal


def _tasks_url(workspace_id: int) -> str:
    return f"/workspaces/{workspace_id}/tasks"


def _task_url(workspace_id: int, task_id: int) -> str:
    return f"{_tasks_url(workspace_id)}/{task_id}"


def _status_url(workspace_id: int, task_id: int) -> str:
    return f"{_task_url(workspace_id, task_id)}/status"


def _assignee_url(workspace_id: int, task_id: int) -> str:
    return f"{_task_url(workspace_id, task_id)}/assignee"


@pytest.fixture()
def workflow_setup(client, user_factory, workspace_factory):
    owner = user_factory("workflow_owner")
    admin = user_factory("workflow_admin")
    member = user_factory("workflow_member")
    second_member = user_factory("workflow_second")
    outsider = user_factory("workflow_outsider")
    workspace = workspace_factory(owner["headers"], "Workflow Workspace")

    for target, role in (
        (admin, "ADMIN"),
        (member, "MEMBER"),
        (second_member, "MEMBER"),
    ):
        response = client.post(
            f"/workspaces/{workspace['id']}/members",
            headers=owner["headers"],
            json={"username": target["username"], "role": role},
        )
        assert response.status_code == 201

    return {
        "owner": owner,
        "admin": admin,
        "member": member,
        "second_member": second_member,
        "outsider": outsider,
        "workspace": workspace,
    }


def _create_task(client, setup, actor="owner", title="Workflow task"):
    response = client.post(
        _tasks_url(setup["workspace"]["id"]),
        headers=setup[actor]["headers"],
        json={"title": title},
    )
    assert response.status_code == 201
    return response.json()


def _set_status(client, setup, task_id, actor, status):
    return client.patch(
        _status_url(setup["workspace"]["id"], task_id),
        headers=setup[actor]["headers"],
        json={"status": status},
    )


def _set_assignee(client, setup, task_id, actor, assignee_id):
    return client.patch(
        _assignee_url(setup["workspace"]["id"], task_id),
        headers=setup[actor]["headers"],
        json={"assignee_id": assignee_id},
    )


def _service_access(
    session,
    workspace_id: int,
    actor_user_id: int,
    task_id: int,
) -> WorkspaceTaskAccess:
    return WorkspaceTaskAccess(
        workspace_access=WorkspaceAccess(
            workspace=session.get(Workspace, workspace_id),
            membership=session.get(
                WorkspaceMember,
                (workspace_id, actor_user_id),
            ),
            current_user=session.get(User, actor_user_id),
        ),
        task=session.get(Task, task_id),
    )


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_manager_can_set_any_status_and_reopen_done(
    client,
    workflow_setup,
    actor,
):
    task = _create_task(client, workflow_setup)
    for requested in ("DONE", "TODO", "IN_PROGRESS", "TODO"):
        response = _set_status(
            client,
            workflow_setup,
            task["id"],
            actor,
            requested,
        )
        assert response.status_code == 200
        assert response.json()["status"] == requested


def test_assigned_member_can_advance_status(client, workflow_setup):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    assert _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        member_id,
    ).status_code == 200

    for requested in ("IN_PROGRESS", "DONE"):
        response = _set_status(
            client,
            workflow_setup,
            task["id"],
            "member",
            requested,
        )
        assert response.status_code == 200
        assert response.json()["status"] == requested


@pytest.mark.parametrize(
    "initial,requested",
    [
        ("TODO", "DONE"),
        ("IN_PROGRESS", "TODO"),
        ("DONE", "TODO"),
    ],
)
def test_member_invalid_status_transition_returns_409(
    client,
    workflow_setup,
    initial,
    requested,
):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    assert _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        member_id,
    ).status_code == 200
    if initial != "TODO":
        assert _set_status(
            client,
            workflow_setup,
            task["id"],
            "owner",
            initial,
        ).status_code == 200

    response = _set_status(
        client,
        workflow_setup,
        task["id"],
        "member",
        requested,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "TASK_STATUS_TRANSITION_CONFLICT"


def test_member_needs_current_assignment_even_when_creator_or_idempotent(
    client,
    workflow_setup,
):
    task = _create_task(client, workflow_setup, actor="member")

    response = _set_status(
        client,
        workflow_setup,
        task["id"],
        "member",
        "TODO",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TASK_PERMISSION_DENIED"


def test_status_access_boundary_and_schema_validation(
    client,
    workflow_setup,
    workspace_factory,
):
    task = _create_task(client, workflow_setup)
    workspace_id = workflow_setup["workspace"]["id"]

    invalid = client.patch(
        _status_url(workspace_id, task["id"]),
        headers=workflow_setup["owner"]["headers"],
        json={"status": "INVALID"},
    )
    assert invalid.status_code == 422

    extra = client.patch(
        _status_url(workspace_id, task["id"]),
        headers=workflow_setup["owner"]["headers"],
        json={"status": "TODO", "assignee_id": 1},
    )
    assert extra.status_code == 422

    outsider_response = _set_status(
        client,
        workflow_setup,
        task["id"],
        "outsider",
        "DONE",
    )
    assert outsider_response.status_code == 404
    assert outsider_response.json()["code"] == "WORKSPACE_NOT_FOUND"

    other_workspace = workspace_factory(
        workflow_setup["outsider"]["headers"],
        "Other Workflow Workspace",
    )
    cross_workspace = client.patch(
        _status_url(other_workspace["id"], task["id"]),
        headers=workflow_setup["outsider"]["headers"],
        json={"status": "DONE"},
    )
    assert cross_workspace.status_code == 404
    assert cross_workspace.json()["code"] == "TASK_NOT_FOUND"


@pytest.mark.parametrize("actor", ["owner", "member"])
def test_idempotent_status_does_not_update_timestamp_or_issue_update(
    client,
    db_session,
    workflow_setup,
    actor,
):
    task = _create_task(client, workflow_setup)
    if actor == "member":
        assert _set_assignee(
            client,
            workflow_setup,
            task["id"],
            "owner",
            workflow_setup["member"]["id"],
        ).status_code == 200
        task = client.get(
            _task_url(workflow_setup["workspace"]["id"], task["id"]),
            headers=workflow_setup["owner"]["headers"],
        ).json()

    updates = []

    def record_task_updates(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("UPDATE TASKS"):
            updates.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record_task_updates)
    try:
        response = _set_status(
            client,
            workflow_setup,
            task["id"],
            actor,
            task["status"],
        )
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            record_task_updates,
        )

    assert response.status_code == 200
    assert response.json()["updated_at"] == task["updated_at"]
    assert updates == []


@pytest.mark.parametrize("actor", ["owner", "admin"])
def test_manager_can_assign_reassign_and_clear_unfinished_task(
    client,
    workflow_setup,
    actor,
):
    task = _create_task(client, workflow_setup)
    first_id = workflow_setup["member"]["id"]
    second_id = workflow_setup["second_member"]["id"]

    for requested in (first_id, second_id, None):
        response = _set_assignee(
            client,
            workflow_setup,
            task["id"],
            actor,
            requested,
        )
        assert response.status_code == 200
        assert response.json()["assignee_id"] == requested


@pytest.mark.parametrize("target", ["outsider", "missing"])
def test_manager_cannot_assign_non_member(
    client,
    workflow_setup,
    target,
):
    task = _create_task(client, workflow_setup)
    target_id = (
        workflow_setup["outsider"]["id"]
        if target == "outsider"
        else 99999999
    )
    response = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        target_id,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_MEMBER_NOT_FOUND"


def test_member_self_claim_conflicts_permissions_and_release(
    client,
    workflow_setup,
):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    second_id = workflow_setup["second_member"]["id"]

    claim = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "member",
        member_id,
    )
    assert claim.status_code == 200

    steal = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "second_member",
        second_id,
    )
    assert steal.status_code == 409
    assert steal.json()["code"] == "TASK_ASSIGNMENT_CONFLICT"

    assign_other = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "member",
        second_id,
    )
    assert assign_other.status_code == 403
    assert assign_other.json()["code"] == "TASK_PERMISSION_DENIED"

    release_other = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "second_member",
        None,
    )
    assert release_other.status_code == 403
    assert release_other.json()["code"] == "TASK_PERMISSION_DENIED"

    release = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "member",
        None,
    )
    assert release.status_code == 200
    assert release.json()["assignee_id"] is None

    release_unassigned = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "member",
        None,
    )
    assert release_unassigned.status_code == 409
    assert release_unassigned.json()["code"] == "TASK_ASSIGNMENT_CONFLICT"


@pytest.mark.parametrize("target", ["outsider", "missing"])
def test_member_cannot_enumerate_target_user_ids(
    client,
    workflow_setup,
    target,
):
    task = _create_task(client, workflow_setup)
    target_id = (
        workflow_setup["outsider"]["id"]
        if target == "outsider"
        else 99999999
    )
    response = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "member",
        target_id,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TASK_PERMISSION_DENIED"


@pytest.mark.parametrize("actor", ["owner", "member"])
def test_idempotent_assignment_does_not_update_timestamp_or_issue_update(
    client,
    db_session,
    workflow_setup,
    actor,
):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    assigned = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        member_id,
    ).json()
    updates = []

    def record_task_updates(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("UPDATE TASKS"):
            updates.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record_task_updates)
    try:
        response = _set_assignee(
            client,
            workflow_setup,
            task["id"],
            actor,
            member_id,
        )
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            record_task_updates,
        )

    assert response.status_code == 200
    assert response.json()["updated_at"] == assigned["updated_at"]
    assert updates == []


def test_done_assignment_is_frozen_but_same_value_is_idempotent(
    client,
    workflow_setup,
):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    second_id = workflow_setup["second_member"]["id"]
    assigned = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        member_id,
    ).json()
    assert _set_status(
        client,
        workflow_setup,
        task["id"],
        "owner",
        "DONE",
    ).status_code == 200
    done = client.get(
        _task_url(workflow_setup["workspace"]["id"], task["id"]),
        headers=workflow_setup["owner"]["headers"],
    ).json()

    for actor in ("owner", "member"):
        same = _set_assignee(
            client,
            workflow_setup,
            task["id"],
            actor,
            member_id,
        )
        assert same.status_code == 200
        assert same.json()["updated_at"] == done["updated_at"]

    for requested in (second_id, None):
        changed = _set_assignee(
            client,
            workflow_setup,
            task["id"],
            "owner",
            requested,
        )
        assert changed.status_code == 409
        assert changed.json()["code"] == "TASK_ASSIGNMENT_CONFLICT"

    reopened = _set_status(
        client,
        workflow_setup,
        task["id"],
        "owner",
        "TODO",
    )
    assert reopened.status_code == 200
    assert reopened.json()["assignee_id"] == assigned["assignee_id"]
    reassigned = _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "admin",
        second_id,
    )
    assert reassigned.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"assignee_id": 0},
        {"assignee_id": -1},
        {"assignee_id": None, "extra": True},
    ],
)
def test_assignee_schema_rejects_invalid_payload(
    client,
    workflow_setup,
    payload,
):
    task = _create_task(client, workflow_setup)
    response = client.patch(
        _assignee_url(workflow_setup["workspace"]["id"], task["id"]),
        headers=workflow_setup["owner"]["headers"],
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("operation", ["status", "assignee"])
@pytest.mark.parametrize("failure_method", ["flush", "refresh", "commit"])
def test_task_workflow_failure_rolls_back_and_session_is_usable(
    client,
    db_session,
    workflow_setup,
    monkeypatch,
    operation,
    failure_method,
):
    task = _create_task(client, workflow_setup)
    workspace_id = workflow_setup["workspace"]["id"]
    access = _service_access(
        db_session,
        workspace_id,
        workflow_setup["owner"]["id"],
        task["id"],
    )

    def fail(*_args, **_kwargs):
        raise SQLAlchemyError(f"simulated {failure_method} failure")

    monkeypatch.setattr(db_session, failure_method, fail)
    with pytest.raises(SQLAlchemyError):
        if operation == "status":
            task_service.update_task_status(
                db_session,
                access,
                TaskStatusUpdate(status=TaskStatus.IN_PROGRESS),
            )
        else:
            task_service.update_task_assignee(
                db_session,
                access,
                TaskAssigneeUpdate(
                    assignee_id=workflow_setup["member"]["id"]
                ),
            )

    assert not db_session.in_transaction()
    persisted = db_session.scalar(
        select(Task)
        .where(Task.id == task["id"])
        .execution_options(populate_existing=True)
    )
    assert persisted.status == TaskStatus.TODO
    assert persisted.assignee_id is None


@pytest.mark.parametrize("operation", ["status", "assignee"])
def test_task_workflow_performs_no_database_io_after_commit(
    client,
    db_session,
    workflow_setup,
    monkeypatch,
    operation,
):
    task = _create_task(client, workflow_setup)
    access = _service_access(
        db_session,
        workflow_setup["workspace"]["id"],
        workflow_setup["owner"]["id"],
        task["id"],
    )
    original_commit = db_session.commit
    committed = False

    def reject_post_commit_io(*_args, **_kwargs):
        if committed:
            raise AssertionError("database IO occurred after commit")

    def tracked_commit():
        nonlocal committed
        original_commit()
        committed = True

    event.listen(
        db_session.get_bind(),
        "before_cursor_execute",
        reject_post_commit_io,
    )
    monkeypatch.setattr(db_session, "commit", tracked_commit)
    try:
        if operation == "status":
            result = task_service.update_task_status(
                db_session,
                access,
                TaskStatusUpdate(status=TaskStatus.IN_PROGRESS),
            )
            assert result["status"] == TaskStatus.IN_PROGRESS
        else:
            result = task_service.update_task_assignee(
                db_session,
                access,
                TaskAssigneeUpdate(
                    assignee_id=workflow_setup["member"]["id"]
                ),
            )
            assert result["assignee_id"] == workflow_setup["member"]["id"]
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            reject_post_commit_io,
        )

    assert committed is True


@pytest.mark.parametrize("operation", ["status", "assignee"])
def test_locked_domain_error_rolls_back_immediately(
    client,
    db_session,
    workflow_setup,
    operation,
):
    task = _create_task(client, workflow_setup)
    member_id = workflow_setup["member"]["id"]
    second_id = workflow_setup["second_member"]["id"]
    assert _set_assignee(
        client,
        workflow_setup,
        task["id"],
        "owner",
        member_id,
    ).status_code == 200
    actor_id = member_id if operation == "status" else second_id
    access = _service_access(
        db_session,
        workflow_setup["workspace"]["id"],
        actor_id,
        task["id"],
    )

    with pytest.raises(AppException) as exc_info:
        if operation == "status":
            task_service.update_task_status(
                db_session,
                access,
                TaskStatusUpdate(status=TaskStatus.DONE),
            )
        else:
            task_service.update_task_assignee(
                db_session,
                access,
                TaskAssigneeUpdate(assignee_id=second_id),
            )

    assert exc_info.value.code in {
        "TASK_STATUS_TRANSITION_CONFLICT",
        "TASK_ASSIGNMENT_CONFLICT",
    }
    assert not db_session.in_transaction()
    assert db_session.get(Task, task["id"]) is not None


def test_two_members_concurrently_claim_only_one_succeeds(
    client,
    workflow_setup,
    monkeypatch,
):
    task = _create_task(client, workflow_setup)
    workspace_id = workflow_setup["workspace"]["id"]
    actor_ids = [
        workflow_setup["member"]["id"],
        workflow_setup["second_member"]["id"],
    ]
    barrier = Barrier(2)
    original_lock_memberships = task_service._lock_workspace_memberships

    def synchronized_lock(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_lock_memberships(*args, **kwargs)

    monkeypatch.setattr(
        task_service,
        "_lock_workspace_memberships",
        synchronized_lock,
    )

    def claim(actor_user_id):
        with TestingSessionLocal() as session:
            access = _service_access(
                session,
                workspace_id,
                actor_user_id,
                task["id"],
            )
            try:
                task_service.update_task_assignee(
                    session,
                    access,
                    TaskAssigneeUpdate(assignee_id=actor_user_id),
                )
                return "success"
            except AppException as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, actor_ids))

    assert sorted(results) == ["TASK_ASSIGNMENT_CONFLICT", "success"]
    with TestingSessionLocal() as verify_session:
        persisted = verify_session.get(Task, task["id"])
        assert persisted.assignee_id in actor_ids


def test_assignment_locks_member_before_removal_and_removal_clears_task(
    client,
    workflow_setup,
    monkeypatch,
):
    task = _create_task(client, workflow_setup)
    workspace_id = workflow_setup["workspace"]["id"]
    owner_id = workflow_setup["owner"]["id"]
    target_id = workflow_setup["member"]["id"]
    assignment_lock_checkpoint = Barrier(2)
    removal_attempt_checkpoint = Barrier(2)
    assignment_has_member_lock = Event()
    allow_assignment_to_continue = Event()
    removal_attempted_member_lock = Event()
    removal_has_member_lock = Event()
    original_assignment_lock = task_service._lock_workspace_memberships
    original_removal_lock = workspace_member_service._lock_workspace_member

    def pause_assignment_after_member_lock(*args, **kwargs):
        memberships = original_assignment_lock(*args, **kwargs)
        assignment_has_member_lock.set()
        assignment_lock_checkpoint.wait(timeout=10)
        assert allow_assignment_to_continue.wait(timeout=10)
        return memberships

    def observe_removal_member_lock(*args, **kwargs):
        removal_attempted_member_lock.set()
        removal_attempt_checkpoint.wait(timeout=10)
        membership = original_removal_lock(*args, **kwargs)
        removal_has_member_lock.set()
        return membership

    monkeypatch.setattr(
        task_service,
        "_lock_workspace_memberships",
        pause_assignment_after_member_lock,
    )
    monkeypatch.setattr(
        workspace_member_service,
        "_lock_workspace_member",
        observe_removal_member_lock,
    )

    def assign():
        with TestingSessionLocal() as session:
            access = _service_access(
                session,
                workspace_id,
                owner_id,
                task["id"],
            )
            task_service.update_task_assignee(
                session,
                access,
                TaskAssigneeUpdate(assignee_id=target_id),
            )
            return "assigned"

    def remove():
        with TestingSessionLocal() as session:
            access = _service_access(
                session,
                workspace_id,
                owner_id,
                task["id"],
            ).workspace_access
            workspace_member_service.remove_member(
                session,
                access,
                target_id,
            )
            return "removed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assignment_future = executor.submit(assign)
        assignment_lock_checkpoint.wait(timeout=10)
        assert assignment_has_member_lock.is_set()
        removal_future = executor.submit(remove)
        removal_attempt_checkpoint.wait(timeout=10)
        assert removal_attempted_member_lock.is_set()
        try:
            assert not removal_has_member_lock.wait(timeout=0.25)
        finally:
            allow_assignment_to_continue.set()
        assert assignment_future.result(timeout=20) == "assigned"
        assert removal_future.result(timeout=20) == "removed"

    with TestingSessionLocal() as verify_session:
        assert verify_session.get(
            WorkspaceMember,
            (workspace_id, target_id),
        ) is None
        orphan_count = verify_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.assignee_id == target_id,
                Task.status != TaskStatus.DONE,
            )
        )
        assert orphan_count == 0


def test_removal_locks_member_before_assignment_and_assignment_gets_404(
    client,
    workflow_setup,
    monkeypatch,
):
    task = _create_task(client, workflow_setup)
    workspace_id = workflow_setup["workspace"]["id"]
    owner_id = workflow_setup["owner"]["id"]
    target_id = workflow_setup["member"]["id"]
    removal_lock_checkpoint = Barrier(2)
    assignment_attempt_checkpoint = Barrier(2)
    removal_has_member_lock = Event()
    allow_removal_to_continue = Event()
    assignment_attempted_member_lock = Event()
    assignment_has_member_lock = Event()
    original_removal_lock = workspace_member_service._lock_workspace_member
    original_assignment_lock = task_service._lock_workspace_memberships

    def pause_removal_after_member_lock(*args, **kwargs):
        membership = original_removal_lock(*args, **kwargs)
        removal_has_member_lock.set()
        removal_lock_checkpoint.wait(timeout=10)
        assert allow_removal_to_continue.wait(timeout=10)
        return membership

    def observe_assignment_member_lock(*args, **kwargs):
        assignment_attempted_member_lock.set()
        assignment_attempt_checkpoint.wait(timeout=10)
        memberships = original_assignment_lock(*args, **kwargs)
        assignment_has_member_lock.set()
        return memberships

    monkeypatch.setattr(
        workspace_member_service,
        "_lock_workspace_member",
        pause_removal_after_member_lock,
    )
    monkeypatch.setattr(
        task_service,
        "_lock_workspace_memberships",
        observe_assignment_member_lock,
    )

    def remove():
        with TestingSessionLocal() as session:
            access = _service_access(
                session,
                workspace_id,
                owner_id,
                task["id"],
            ).workspace_access
            workspace_member_service.remove_member(session, access, target_id)
            return "removed"

    def assign():
        with TestingSessionLocal() as session:
            access = _service_access(
                session,
                workspace_id,
                owner_id,
                task["id"],
            )
            try:
                task_service.update_task_assignee(
                    session,
                    access,
                    TaskAssigneeUpdate(assignee_id=target_id),
                )
                return "assigned"
            except AppException as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        removal_future = executor.submit(remove)
        removal_lock_checkpoint.wait(timeout=10)
        assert removal_has_member_lock.is_set()
        assignment_future = executor.submit(assign)
        assignment_attempt_checkpoint.wait(timeout=10)
        assert assignment_attempted_member_lock.is_set()
        try:
            assert not assignment_has_member_lock.wait(timeout=0.25)
        finally:
            allow_removal_to_continue.set()
        assert removal_future.result(timeout=20) == "removed"
        assert (
            assignment_future.result(timeout=20)
            == "WORKSPACE_MEMBER_NOT_FOUND"
        )

    with TestingSessionLocal() as verify_session:
        assert verify_session.get(
            WorkspaceMember,
            (workspace_id, target_id),
        ) is None
        orphan_count = verify_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.workspace_id == workspace_id,
                Task.assignee_id == target_id,
            )
        )
        assert orphan_count == 0
