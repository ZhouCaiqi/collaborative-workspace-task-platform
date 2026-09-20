from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.dependencies import WorkspaceAccess, WorkspaceTaskAccess
from app.enums import MemberRole, TaskStatus
from app.models import Task, User, Workspace, WorkspaceMember
from app.schemas import TaskCreate, TaskUpdate
from app.services import task_service


def _tasks_url(workspace_id: int) -> str:
    return f"/workspaces/{workspace_id}/tasks"


def _task_url(workspace_id: int, task_id: int) -> str:
    return f"{_tasks_url(workspace_id)}/{task_id}"


def _add_member(client, workspace_id, owner, target, role):
    response = client.post(
        f"/workspaces/{workspace_id}/members",
        headers=owner["headers"],
        json={"username": target["username"], "role": role},
    )
    assert response.status_code == 201


@pytest.fixture()
def task_setup(client, user_factory, workspace_factory):
    owner = user_factory("task_owner")
    admin = user_factory("task_admin")
    member = user_factory("task_member")
    second_member = user_factory("task_second_member")
    outsider = user_factory("task_outsider")
    workspace = workspace_factory(owner["headers"], "Task Workspace")
    _add_member(client, workspace["id"], owner, admin, "ADMIN")
    _add_member(client, workspace["id"], owner, member, "MEMBER")
    _add_member(
        client,
        workspace["id"],
        owner,
        second_member,
        "MEMBER",
    )
    return {
        "owner": owner,
        "admin": admin,
        "member": member,
        "second_member": second_member,
        "outsider": outsider,
        "workspace": workspace,
    }


def _create_task(
    client,
    setup,
    actor="owner",
    title="Collaborative task",
    priority=1,
    description=None,
):
    response = client.post(
        _tasks_url(setup["workspace"]["id"]),
        headers=setup[actor]["headers"],
        json={
            "title": title,
            "description": description,
            "priority": priority,
        },
    )
    assert response.status_code == 201
    return response.json()


def _workspace_access(db_session, setup, actor) -> WorkspaceAccess:
    workspace_id = setup["workspace"]["id"]
    user_id = setup[actor]["id"]
    return WorkspaceAccess(
        workspace=db_session.get(Workspace, workspace_id),
        membership=db_session.get(WorkspaceMember, (workspace_id, user_id)),
        current_user=db_session.get(User, user_id),
    )


def _workspace_task_access(
    db_session,
    setup,
    actor,
    task_id,
) -> WorkspaceTaskAccess:
    return WorkspaceTaskAccess(
        workspace_access=_workspace_access(db_session, setup, actor),
        task=db_session.get(Task, task_id),
    )


@pytest.mark.parametrize("actor", ["owner", "admin", "member"])
def test_every_workspace_role_can_create_task(client, task_setup, actor):
    response = client.post(
        _tasks_url(task_setup["workspace"]["id"]),
        headers=task_setup[actor]["headers"],
        json={
            "title": "  Created by role  ",
            "description": "Details",
            "priority": 3,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Created by role"
    assert body["workspace_id"] == task_setup["workspace"]["id"]
    assert body["creator_id"] == task_setup[actor]["id"]
    assert body["assignee_id"] is None
    assert body["status"] == "TODO"
    assert body["created_at"]
    assert body["updated_at"]
    assert "owner_id" not in body
    assert "completed" not in body


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", 1),
        ("creator_id", 1),
        ("assignee_id", 1),
        ("status", "DONE"),
        ("created_at", "2026-01-01T00:00:00"),
        ("updated_at", "2026-01-01T00:00:00"),
    ],
)
def test_create_rejects_server_controlled_fields(
    client,
    task_setup,
    field,
    value,
):
    response = client.post(
        _tasks_url(task_setup["workspace"]["id"]),
        headers=task_setup["owner"]["headers"],
        json={"title": "Invalid extra field", field: value},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("actor", ["owner", "admin", "member"])
def test_every_member_can_view_task(client, task_setup, actor):
    task = _create_task(client, task_setup)
    response = client.get(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup[actor]["headers"],
    )
    assert response.status_code == 200
    assert response.json() == task


def test_non_member_gets_workspace_404_before_task_lookup(client, task_setup):
    response = client.get(
        _task_url(task_setup["workspace"]["id"], 999999),
        headers=task_setup["outsider"]["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_NOT_FOUND"


def test_member_gets_task_404_for_missing_task(client, task_setup):
    response = client.get(
        _task_url(task_setup["workspace"]["id"], 999999),
        headers=task_setup["member"]["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_cross_workspace_task_id_returns_task_404(
    client,
    task_setup,
    workspace_factory,
):
    task = _create_task(client, task_setup)
    other_workspace = workspace_factory(
        task_setup["outsider"]["headers"],
        "Other Workspace",
    )
    response = client.get(
        _task_url(other_workspace["id"], task["id"]),
        headers=task_setup["outsider"]["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_task_list_filters_paginates_and_isolates_workspaces(
    client,
    db_session,
    task_setup,
    workspace_factory,
):
    first = _create_task(client, task_setup, title="First", priority=1)
    second = _create_task(client, task_setup, title="Second", priority=1)
    third = _create_task(client, task_setup, title="Third", priority=3)

    second_model = db_session.get(Task, second["id"])
    second_model.status = TaskStatus.DONE
    second_model.assignee_id = task_setup["member"]["id"]
    third_model = db_session.get(Task, third["id"])
    third_model.assignee_id = task_setup["admin"]["id"]
    db_session.commit()

    other_workspace = workspace_factory(
        task_setup["outsider"]["headers"],
        "Isolated Workspace",
    )
    other_response = client.post(
        _tasks_url(other_workspace["id"]),
        headers=task_setup["outsider"]["headers"],
        json={"title": "Must stay isolated"},
    )
    assert other_response.status_code == 201

    base_url = _tasks_url(task_setup["workspace"]["id"])
    headers = task_setup["owner"]["headers"]

    status_response = client.get(
        base_url,
        headers=headers,
        params={"status": "DONE"},
    )
    assert [item["id"] for item in status_response.json()["items"]] == [
        second["id"]
    ]

    priority_response = client.get(
        base_url,
        headers=headers,
        params={"priority": 3},
    )
    assert priority_response.json()["total"] == 1
    assert priority_response.json()["items"][0]["id"] == third["id"]

    assignee_response = client.get(
        base_url,
        headers=headers,
        params={"assignee_id": task_setup["member"]["id"]},
    )
    assert [item["id"] for item in assignee_response.json()["items"]] == [
        second["id"]
    ]

    unassigned_response = client.get(
        base_url,
        headers=headers,
        params={"unassigned": "true"},
    )
    assert [item["id"] for item in unassigned_response.json()["items"]] == [
        first["id"]
    ]

    pagination_response = client.get(
        base_url,
        headers=headers,
        params={
            "limit": 2,
            "offset": 1,
            "sort_by": "id",
            "sort_order": "asc",
        },
    )
    assert pagination_response.json()["total"] == 3
    assert [
        item["id"] for item in pagination_response.json()["items"]
    ] == [second["id"], third["id"]]

    stable_response = client.get(
        base_url,
        headers=headers,
        params={"sort_by": "priority", "sort_order": "asc"},
    )
    assert [item["id"] for item in stable_response.json()["items"]] == [
        first["id"],
        second["id"],
        third["id"],
    ]


def test_conflicting_assignee_filters_return_422(client, task_setup):
    response = client.get(
        _tasks_url(task_setup["workspace"]["id"]),
        headers=task_setup["owner"]["headers"],
        params={
            "assignee_id": task_setup["member"]["id"],
            "unassigned": "true",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"priority": 6},
        {"status": "INVALID"},
        {"sort_by": "title"},
        {"sort_order": "unknown"},
    ],
)
def test_invalid_task_list_query_returns_422(client, task_setup, params):
    response = client.get(
        _tasks_url(task_setup["workspace"]["id"]),
        headers=task_setup["owner"]["headers"],
        params=params,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "actor,creator",
    [
        ("owner", "member"),
        ("admin", "member"),
        ("member", "member"),
    ],
)
def test_authorized_roles_can_edit_task(
    client,
    task_setup,
    actor,
    creator,
):
    task = _create_task(client, task_setup, actor=creator)
    response = client.patch(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup[actor]["headers"],
        json={"title": "  Updated title  ", "priority": 4},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Updated title"
    assert response.json()["priority"] == 4


def test_member_cannot_edit_another_creators_task(client, task_setup):
    task = _create_task(client, task_setup, actor="second_member")
    response = client.patch(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup["member"]["headers"],
        json={"title": "Forbidden"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TASK_PERMISSION_DENIED"


@pytest.mark.parametrize(
    "actor,creator",
    [
        ("owner", "member"),
        ("admin", "member"),
        ("member", "member"),
    ],
)
def test_authorized_roles_can_delete_task(
    client,
    task_setup,
    actor,
    creator,
):
    task = _create_task(client, task_setup, actor=creator)
    response = client.delete(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup[actor]["headers"],
    )
    assert response.status_code == 204
    assert response.content == b""


def test_member_cannot_delete_another_creators_task(client, task_setup):
    task = _create_task(client, task_setup, actor="second_member")
    response = client.delete(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup["member"]["headers"],
    )
    assert response.status_code == 403
    assert response.json()["code"] == "TASK_PERMISSION_DENIED"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "DONE"},
        {"assignee_id": 1},
        {},
        {"title": None},
        {"priority": None},
        {"title": "   "},
        {"title": "x" * 101},
        {"priority": 0},
        {"priority": 6},
    ],
)
def test_invalid_general_task_updates_return_422(
    client,
    task_setup,
    payload,
):
    task = _create_task(client, task_setup)
    response = client.patch(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup["owner"]["headers"],
        json=payload,
    )
    assert response.status_code == 422


def test_description_can_be_cleared_with_null(client, task_setup):
    task = _create_task(
        client,
        task_setup,
        description="Description to clear",
    )
    response = client.patch(
        _task_url(task_setup["workspace"]["id"], task["id"]),
        headers=task_setup["owner"]["headers"],
        json={"description": None},
    )
    assert response.status_code == 200
    assert response.json()["description"] is None


@pytest.mark.parametrize("failure_method", ["flush", "refresh", "commit"])
def test_create_failure_rolls_back_and_session_remains_usable(
    db_session,
    task_setup,
    monkeypatch,
    failure_method,
):
    access = _workspace_access(db_session, task_setup, "owner")

    def fail(*args, **kwargs):
        raise SQLAlchemyError(f"simulated {failure_method} failure")

    monkeypatch.setattr(db_session, failure_method, fail)
    with pytest.raises(SQLAlchemyError):
        task_service.create_task(
            db=db_session,
            access=access,
            data=TaskCreate(title="Must roll back"),
        )

    assert not db_session.in_transaction()
    assert db_session.scalar(
        select(func.count()).select_from(Task)
    ) == 0


@pytest.mark.parametrize("failure_method", ["flush", "refresh", "commit"])
def test_update_failure_rolls_back_and_preserves_task(
    client,
    db_session,
    task_setup,
    monkeypatch,
    failure_method,
):
    task = _create_task(client, task_setup, title="Original title")
    access = _workspace_task_access(
        db_session,
        task_setup,
        "owner",
        task["id"],
    )

    def fail(*args, **kwargs):
        raise SQLAlchemyError(f"simulated {failure_method} failure")

    monkeypatch.setattr(db_session, failure_method, fail)
    with pytest.raises(SQLAlchemyError):
        task_service.update_task(
            db=db_session,
            access=access,
            data=TaskUpdate(title="Changed title"),
        )

    assert not db_session.in_transaction()
    assert db_session.get(Task, task["id"]).title == "Original title"


@pytest.mark.parametrize("failure_method", ["flush", "commit"])
def test_delete_failure_rolls_back_and_preserves_task(
    client,
    db_session,
    task_setup,
    monkeypatch,
    failure_method,
):
    task = _create_task(client, task_setup)
    access = _workspace_task_access(
        db_session,
        task_setup,
        "owner",
        task["id"],
    )

    def fail(*args, **kwargs):
        raise SQLAlchemyError(f"simulated {failure_method} failure")

    monkeypatch.setattr(db_session, failure_method, fail)
    with pytest.raises(SQLAlchemyError):
        task_service.delete_task(db=db_session, access=access)

    assert not db_session.in_transaction()
    assert db_session.get(Task, task["id"]) is not None


def test_create_performs_no_database_io_after_commit(
    db_session,
    task_setup,
    monkeypatch,
):
    access = _workspace_access(db_session, task_setup, "owner")
    original_commit = db_session.commit
    committed = False

    def detect_post_commit_sql(*args, **kwargs):
        if committed:
            raise AssertionError("database IO occurred after commit")

    def tracked_commit():
        nonlocal committed
        original_commit()
        committed = True

    event.listen(
        db_session.get_bind(),
        "before_cursor_execute",
        detect_post_commit_sql,
    )
    monkeypatch.setattr(db_session, "commit", tracked_commit)
    try:
        result = task_service.create_task(
            db=db_session,
            access=access,
            data=TaskCreate(title="No post-commit IO"),
        )
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            detect_post_commit_sql,
        )

    assert result["title"] == "No post-commit IO"
    assert committed is True


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_existing_task_writes_perform_no_database_io_after_commit(
    client,
    db_session,
    task_setup,
    monkeypatch,
    operation,
):
    task = _create_task(client, task_setup, title="Existing task")
    access = _workspace_task_access(
        db_session,
        task_setup,
        "owner",
        task["id"],
    )
    original_commit = db_session.commit
    committed = False

    def detect_post_commit_sql(*args, **kwargs):
        if committed:
            raise AssertionError("database IO occurred after commit")

    def tracked_commit():
        nonlocal committed
        original_commit()
        committed = True

    event.listen(
        db_session.get_bind(),
        "before_cursor_execute",
        detect_post_commit_sql,
    )
    monkeypatch.setattr(db_session, "commit", tracked_commit)
    try:
        if operation == "update":
            result = task_service.update_task(
                db=db_session,
                access=access,
                data=TaskUpdate(title="Updated without post-commit IO"),
            )
            assert result["title"] == "Updated without post-commit IO"
        else:
            task_service.delete_task(db=db_session, access=access)
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            detect_post_commit_sql,
        )

    assert committed is True


@pytest.mark.parametrize(
    "column,value",
    [
        ("workspace_id", None),
        ("creator_id", None),
        ("status", None),
        ("created_at", None),
        ("updated_at", None),
    ],
)
def test_database_rejects_null_required_task_fields(
    db_session,
    task_setup,
    column,
    value,
):
    values = {
        "title": "Constraint task",
        "description": None,
        "priority": 1,
        "workspace_id": task_setup["workspace"]["id"],
        "creator_id": task_setup["owner"]["id"],
        "assignee_id": None,
        "status": "TODO",
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    values[column] = value
    with pytest.raises(DBAPIError):
        db_session.execute(
            text(
                "INSERT INTO tasks "
                "(title, description, priority, workspace_id, creator_id, "
                "assignee_id, status, created_at, updated_at) VALUES "
                "(:title, :description, :priority, :workspace_id, "
                ":creator_id, :assignee_id, :status, :created_at, "
                ":updated_at)"
            ),
            values,
        )
        db_session.commit()
    db_session.rollback()


def test_openapi_contains_nested_task_crud_and_workflow_routes(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/tasks/" not in paths
    assert "/tasks/{task_id}" not in paths
    assert "/workspaces/{workspace_id}/tasks" in paths
    assert "/workspaces/{workspace_id}/tasks/{task_id}" in paths
    assert "/workspaces/{workspace_id}/tasks/{task_id}/status" in paths
    assert "/workspaces/{workspace_id}/tasks/{task_id}/assignee" in paths
