import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from app.dependencies import WorkspaceAccess
from app.enums import MemberRole
from app.models import User, Workspace, WorkspaceMember
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
