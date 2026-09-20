import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from app.dependencies import WorkspaceAccess
from app.enums import MemberRole, TaskStatus
from app.exceptions import OwnerMembershipConflictError
from app.models import Task, User, Workspace, WorkspaceMember
from app.services import workspace_member_service


def _add_member(client, workspace_id, actor, target, role="MEMBER"):
    return client.post(
        f"/workspaces/{workspace_id}/members",
        headers=actor["headers"],
        json={"username": target["username"], "role": role},
    )


def _get_workspace_access(db_session, workspace_id, user_id):
    return WorkspaceAccess(
        workspace=db_session.get(Workspace, workspace_id),
        membership=db_session.get(
            WorkspaceMember,
            (workspace_id, user_id),
        ),
        current_user=db_session.get(User, user_id),
    )


@pytest.fixture()
def workspace_roles(client, user_factory, workspace_factory):
    owner = user_factory("owner")
    admin = user_factory("admin")
    member = user_factory("member")
    outsider = user_factory("outsider")
    workspace = workspace_factory(owner["headers"])

    assert _add_member(
        client,
        workspace["id"],
        owner,
        admin,
        "ADMIN",
    ).status_code == 201
    assert _add_member(
        client,
        workspace["id"],
        owner,
        member,
    ).status_code == 201

    return {
        "workspace": workspace,
        "owner": owner,
        "admin": admin,
        "member": member,
        "outsider": outsider,
    }


@pytest.mark.parametrize("role", ["MEMBER", "ADMIN"])
def test_owner_can_add_member_or_admin(
    client,
    user_factory,
    workspace_factory,
    role,
):
    owner = user_factory("owner")
    target = user_factory("target")
    workspace = workspace_factory(owner["headers"])

    response = _add_member(
        client,
        workspace["id"],
        owner,
        target,
        role,
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == target["id"]
    assert response.json()["role"] == role


def test_admin_can_add_member(client, user_factory, workspace_roles):
    target = user_factory("target")
    data = workspace_roles

    response = _add_member(
        client,
        data["workspace"]["id"],
        data["admin"],
        target,
    )

    assert response.status_code == 201
    assert response.json()["role"] == "MEMBER"


def test_admin_cannot_add_admin(client, user_factory, workspace_roles):
    target = user_factory("target")
    data = workspace_roles

    response = _add_member(
        client,
        data["workspace"]["id"],
        data["admin"],
        target,
        "ADMIN",
    )

    assert response.status_code == 403
    assert response.json()["code"] == "WORKSPACE_PERMISSION_DENIED"


def test_member_cannot_add_member(client, user_factory, workspace_roles):
    target = user_factory("target")
    data = workspace_roles

    response = _add_member(
        client,
        data["workspace"]["id"],
        data["member"],
        target,
    )

    assert response.status_code == 403


def test_client_cannot_request_owner_role(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    target = user_factory("target")
    workspace = workspace_factory(owner["headers"])

    response = _add_member(
        client,
        workspace["id"],
        owner,
        target,
        "OWNER",
    )

    assert response.status_code == 422


def test_duplicate_member_returns_409(client, workspace_roles):
    data = workspace_roles

    response = _add_member(
        client,
        data["workspace"]["id"],
        data["owner"],
        data["member"],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "WORKSPACE_MEMBER_ALREADY_EXISTS"


def test_any_member_can_list_members(client, workspace_roles):
    data = workspace_roles

    for viewer in (data["owner"], data["admin"], data["member"]):
        response = client.get(
            f"/workspaces/{data['workspace']['id']}/members",
            headers=viewer["headers"],
        )
        assert response.status_code == 200
        assert response.json()["total"] == 3
        assert {item["role"] for item in response.json()["items"]} == {
            "OWNER",
            "ADMIN",
            "MEMBER",
        }


def test_owner_can_change_admin_and_member_roles(client, workspace_roles):
    data = workspace_roles
    path = f"/workspaces/{data['workspace']['id']}/members"

    promote = client.patch(
        f"{path}/{data['member']['id']}",
        headers=data["owner"]["headers"],
        json={"role": "ADMIN"},
    )
    demote = client.patch(
        f"{path}/{data['admin']['id']}",
        headers=data["owner"]["headers"],
        json={"role": "MEMBER"},
    )

    assert promote.status_code == 200
    assert promote.json()["role"] == "ADMIN"
    assert demote.status_code == 200
    assert demote.json()["role"] == "MEMBER"


@pytest.mark.parametrize("actor_name", ["admin", "member"])
def test_non_owner_cannot_change_roles(client, workspace_roles, actor_name):
    data = workspace_roles

    response = client.patch(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data['member']['id']}"
        ),
        headers=data[actor_name]["headers"],
        json={"role": "ADMIN"},
    )

    assert response.status_code == 403


def test_owner_cannot_be_demoted(client, workspace_roles):
    data = workspace_roles

    response = client.patch(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data['owner']['id']}"
        ),
        headers=data["owner"]["headers"],
        json={"role": "MEMBER"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "OWNER_MEMBERSHIP_CONFLICT"


@pytest.mark.parametrize("target_name", ["member", "admin"])
def test_owner_can_remove_member_or_admin(
    client,
    workspace_roles,
    target_name,
):
    data = workspace_roles

    response = client.delete(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data[target_name]['id']}"
        ),
        headers=data["owner"]["headers"],
    )

    assert response.status_code == 204
    assert response.content == b""


def test_admin_can_remove_member(client, workspace_roles):
    data = workspace_roles

    response = client.delete(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data['member']['id']}"
        ),
        headers=data["admin"]["headers"],
    )

    assert response.status_code == 204


@pytest.mark.parametrize("target_name", ["admin", "owner"])
def test_admin_cannot_remove_admin_or_owner(
    client,
    workspace_roles,
    target_name,
):
    data = workspace_roles

    response = client.delete(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data[target_name]['id']}"
        ),
        headers=data["admin"]["headers"],
    )

    assert response.status_code == 403


def test_member_cannot_remove_member(client, workspace_roles):
    data = workspace_roles

    response = client.delete(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data['admin']['id']}"
        ),
        headers=data["member"]["headers"],
    )

    assert response.status_code == 403


def test_owner_cannot_remove_self(client, workspace_roles):
    data = workspace_roles

    response = client.delete(
        (
            f"/workspaces/{data['workspace']['id']}/members/"
            f"{data['owner']['id']}"
        ),
        headers=data["owner"]["headers"],
    )

    assert response.status_code == 409
    assert response.json()["code"] == "OWNER_MEMBERSHIP_CONFLICT"


@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_non_member_gets_404_before_target_lookup(
    client,
    workspace_roles,
    method,
):
    data = workspace_roles
    base = f"/workspaces/{data['workspace']['id']}/members"
    request_kwargs = {"headers": data["outsider"]["headers"]}

    if method == "get":
        response = client.get(base, **request_kwargs)
    elif method == "post":
        response = client.post(
            base,
            json={"username": "does_not_exist", "role": "MEMBER"},
            **request_kwargs,
        )
    elif method == "patch":
        response = client.patch(
            f"{base}/999999",
            json={"role": "MEMBER"},
            **request_kwargs,
        )
    else:
        response = client.delete(f"{base}/999999", **request_kwargs)

    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_NOT_FOUND"


def test_authorized_owner_sees_target_user_not_found(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    workspace = workspace_factory(owner["headers"])

    response = client.post(
        f"/workspaces/{workspace['id']}/members",
        headers=owner["headers"],
        json={"username": "does_not_exist", "role": "MEMBER"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "USER_NOT_FOUND"


def test_authorized_owner_gets_404_for_missing_membership(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    non_member = user_factory("non_member")
    workspace = workspace_factory(owner["headers"])

    response = client.delete(
        f"/workspaces/{workspace['id']}/members/{non_member['id']}",
        headers=owner["headers"],
    )

    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_MEMBER_NOT_FOUND"


def test_database_rejects_duplicate_membership(
    client,
    db_session,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    workspace = workspace_factory(owner["headers"])

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO workspace_members "
                "(workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, 'MEMBER')"
            ),
            {"workspace_id": workspace["id"], "user_id": owner["id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_database_rejects_invalid_role(
    client,
    db_session,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    target = user_factory("target")
    workspace = workspace_factory(owner["headers"])

    with pytest.raises(DBAPIError):
        db_session.execute(
            text(
                "INSERT INTO workspace_members "
                "(workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, 'INVALID')"
            ),
            {"workspace_id": workspace["id"], "user_id": target["id"]},
        )
        db_session.commit()
    db_session.rollback()


def test_add_member_refresh_failure_rolls_back_membership(
    db_session,
    user_factory,
    workspace_factory,
    monkeypatch,
):
    owner = user_factory("owner")
    target = user_factory("target")
    workspace = workspace_factory(owner["headers"])
    access = _get_workspace_access(
        db_session,
        workspace["id"],
        owner["id"],
    )

    def fail_refresh(instance):
        raise SQLAlchemyError("simulated membership refresh failure")

    monkeypatch.setattr(db_session, "refresh", fail_refresh)

    with pytest.raises(SQLAlchemyError):
        workspace_member_service.add_member(
            db=db_session,
            access=access,
            username=target["username"],
            role=MemberRole.MEMBER,
        )

    assert not db_session.in_transaction()
    assert db_session.get(
        WorkspaceMember,
        (workspace["id"], target["id"]),
    ) is None


def test_update_role_refresh_failure_preserves_original_role(
    client,
    db_session,
    user_factory,
    workspace_factory,
    monkeypatch,
):
    owner = user_factory("owner")
    target = user_factory("target")
    workspace = workspace_factory(owner["headers"])
    assert _add_member(
        client,
        workspace["id"],
        owner,
        target,
    ).status_code == 201
    access = _get_workspace_access(
        db_session,
        workspace["id"],
        owner["id"],
    )

    def fail_refresh(instance):
        raise SQLAlchemyError("simulated role refresh failure")

    monkeypatch.setattr(db_session, "refresh", fail_refresh)

    with pytest.raises(SQLAlchemyError):
        workspace_member_service.update_member_role(
            db=db_session,
            access=access,
            user_id=target["id"],
            role=MemberRole.ADMIN,
        )

    assert not db_session.in_transaction()
    membership = db_session.get(
        WorkspaceMember,
        (workspace["id"], target["id"]),
    )
    assert membership.role == MemberRole.MEMBER


def test_remove_member_unassigns_only_unfinished_tasks_in_workspace(
    client,
    db_session,
    workspace_roles,
    workspace_factory,
):
    data = workspace_roles
    workspace_id = data["workspace"]["id"]
    target_id = data["member"]["id"]
    other_workspace = workspace_factory(
        data["owner"]["headers"],
        "Other Member Removal Workspace",
    )
    assert _add_member(
        client,
        other_workspace["id"],
        data["owner"],
        data["member"],
    ).status_code == 201

    tasks = [
        Task(
            title="Target TODO",
            workspace_id=workspace_id,
            creator_id=target_id,
            assignee_id=target_id,
            status=TaskStatus.TODO,
        ),
        Task(
            title="Target in progress",
            workspace_id=workspace_id,
            creator_id=data["owner"]["id"],
            assignee_id=target_id,
            status=TaskStatus.IN_PROGRESS,
        ),
        Task(
            title="Target done",
            workspace_id=workspace_id,
            creator_id=data["owner"]["id"],
            assignee_id=target_id,
            status=TaskStatus.DONE,
        ),
        Task(
            title="Other member task",
            workspace_id=workspace_id,
            creator_id=data["owner"]["id"],
            assignee_id=data["admin"]["id"],
            status=TaskStatus.TODO,
        ),
        Task(
            title="Other workspace task",
            workspace_id=other_workspace["id"],
            creator_id=data["owner"]["id"],
            assignee_id=target_id,
            status=TaskStatus.TODO,
        ),
    ]
    db_session.add_all(tasks)
    db_session.commit()
    task_ids = [task.id for task in tasks]

    response = client.delete(
        f"/workspaces/{workspace_id}/members/{target_id}",
        headers=data["owner"]["headers"],
    )
    assert response.status_code == 204

    persisted = {
        task.id: task
        for task in db_session.scalars(
            select(Task)
            .where(Task.id.in_(task_ids))
            .execution_options(populate_existing=True)
        )
    }
    assert persisted[task_ids[0]].assignee_id is None
    assert persisted[task_ids[1]].assignee_id is None
    assert persisted[task_ids[2]].assignee_id == target_id
    assert persisted[task_ids[3]].assignee_id == data["admin"]["id"]
    assert persisted[task_ids[4]].assignee_id == target_id
    assert persisted[task_ids[0]].creator_id == target_id
    assert db_session.get(
        WorkspaceMember,
        (workspace_id, target_id),
    ) is None
    assert db_session.get(
        WorkspaceMember,
        (other_workspace["id"], target_id),
    ) is not None


def test_remove_member_uses_one_bulk_update_flush_and_commit(
    db_session,
    workspace_roles,
    monkeypatch,
):
    data = workspace_roles
    workspace_id = data["workspace"]["id"]
    target_id = data["member"]["id"]
    task = Task(
        title="Bulk cleanup task",
        workspace_id=workspace_id,
        creator_id=data["owner"]["id"],
        assignee_id=target_id,
        status=TaskStatus.TODO,
    )
    db_session.add(task)
    db_session.commit()
    access = _get_workspace_access(
        db_session,
        workspace_id,
        data["owner"]["id"],
    )
    original_flush = db_session.flush
    original_commit = db_session.commit
    flush_count = 0
    commit_count = 0
    task_updates = []

    def tracked_flush(*args, **kwargs):
        nonlocal flush_count
        flush_count += 1
        return original_flush(*args, **kwargs)

    def tracked_commit():
        nonlocal commit_count
        commit_count += 1
        return original_commit()

    def record_updates(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("UPDATE TASKS"):
            task_updates.append(statement)

    monkeypatch.setattr(db_session, "flush", tracked_flush)
    monkeypatch.setattr(db_session, "commit", tracked_commit)
    event.listen(db_session.get_bind(), "before_cursor_execute", record_updates)
    try:
        workspace_member_service.remove_member(
            db_session,
            access,
            target_id,
        )
    finally:
        event.remove(
            db_session.get_bind(),
            "before_cursor_execute",
            record_updates,
        )

    assert flush_count == 1
    assert commit_count == 1
    assert len(task_updates) == 1


@pytest.mark.parametrize("failure_point", ["update", "flush", "commit"])
def test_remove_member_failure_rolls_back_all_changes(
    db_session,
    workspace_roles,
    monkeypatch,
    failure_point,
):
    data = workspace_roles
    workspace_id = data["workspace"]["id"]
    target_id = data["member"]["id"]
    task = Task(
        title="Removal rollback task",
        workspace_id=workspace_id,
        creator_id=data["owner"]["id"],
        assignee_id=target_id,
        status=TaskStatus.IN_PROGRESS,
    )
    db_session.add(task)
    db_session.commit()
    task_id = task.id
    access = _get_workspace_access(
        db_session,
        workspace_id,
        data["owner"]["id"],
    )

    def fail(*_args, **_kwargs):
        raise SQLAlchemyError(f"simulated {failure_point} failure")

    if failure_point == "update":
        original_execute = db_session.execute

        def fail_bulk_update(statement, *args, **kwargs):
            if getattr(statement, "is_update", False):
                fail()
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", fail_bulk_update)
    else:
        monkeypatch.setattr(db_session, failure_point, fail)

    with pytest.raises(SQLAlchemyError):
        workspace_member_service.remove_member(
            db_session,
            access,
            target_id,
        )

    assert not db_session.in_transaction()
    membership = db_session.get(
        WorkspaceMember,
        (workspace_id, target_id),
    )
    persisted_task = db_session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .execution_options(populate_existing=True)
    )
    assert membership is not None
    assert persisted_task.assignee_id == target_id


def test_remove_member_domain_error_releases_locked_transaction(
    db_session,
    workspace_roles,
):
    data = workspace_roles
    access = _get_workspace_access(
        db_session,
        data["workspace"]["id"],
        data["owner"]["id"],
    )

    with pytest.raises(OwnerMembershipConflictError):
        workspace_member_service.remove_member(
            db_session,
            access,
            data["owner"]["id"],
        )

    assert not db_session.in_transaction()
    assert db_session.get(
        WorkspaceMember,
        (data["workspace"]["id"], data["owner"]["id"]),
    ) is not None
