import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.enums import MemberRole
from app.models import User, Workspace, WorkspaceMember
from app.services import workspace_service


def test_create_workspace_creates_single_owner_membership(
    client,
    db_session,
    user_factory,
):
    owner = user_factory("owner")

    response = client.post(
        "/workspaces",
        headers=owner["headers"],
        json={"name": "  Product Team  "},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Product Team"
    assert body["created_by_id"] == owner["id"]
    assert body["current_role"] == "OWNER"
    assert body["created_at"]
    assert body["updated_at"]

    memberships = db_session.scalars(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == body["id"]
        )
    ).all()
    assert len(memberships) == 1
    assert memberships[0].user_id == owner["id"]
    assert memberships[0].role == MemberRole.OWNER


def test_workspace_list_only_returns_current_users_memberships(
    client,
    user_factory,
    workspace_factory,
):
    first_user = user_factory("first")
    second_user = user_factory("second")
    first_workspace = workspace_factory(
        first_user["headers"],
        "First Workspace",
    )
    workspace_factory(second_user["headers"], "Second Workspace")

    response = client.get(
        "/workspaces?limit=10&offset=0",
        headers=first_user["headers"],
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [first_workspace],
        "total": 1,
        "limit": 10,
        "offset": 0,
    }


@pytest.mark.parametrize("role", ["OWNER", "ADMIN", "MEMBER"])
def test_all_workspace_roles_can_get_workspace(
    client,
    user_factory,
    workspace_factory,
    role,
):
    owner = user_factory("owner")
    workspace = workspace_factory(owner["headers"])

    if role == "OWNER":
        viewer = owner
    else:
        viewer = user_factory(role.casefold())
        add_response = client.post(
            f"/workspaces/{workspace['id']}/members",
            headers=owner["headers"],
            json={"username": viewer["username"], "role": role},
        )
        assert add_response.status_code == 201

    response = client.get(
        f"/workspaces/{workspace['id']}",
        headers=viewer["headers"],
    )

    assert response.status_code == 200
    assert response.json()["id"] == workspace["id"]
    assert response.json()["current_role"] == role


def test_non_member_gets_404_for_existing_workspace(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("owner")
    outsider = user_factory("outsider")
    workspace = workspace_factory(owner["headers"])

    response = client.get(
        f"/workspaces/{workspace['id']}",
        headers=outsider["headers"],
    )

    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_NOT_FOUND"


@pytest.mark.parametrize("name", ["   ", "x" * 101])
def test_workspace_name_validation_returns_422(
    client,
    user_factory,
    name,
):
    owner = user_factory("owner")

    response = client.post(
        "/workspaces",
        headers=owner["headers"],
        json={"name": name},
    )

    assert response.status_code == 422


def test_duplicate_workspace_names_are_allowed(
    client,
    user_factory,
):
    owner = user_factory("owner")

    first = client.post(
        "/workspaces",
        headers=owner["headers"],
        json={"name": "Shared Name"},
    )
    second = client.post(
        "/workspaces",
        headers=owner["headers"],
        json={"name": "Shared Name"},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_owner_membership_failure_rolls_back_workspace(
    db_session,
    user_factory,
    monkeypatch,
):
    owner_data = user_factory("owner")
    owner = db_session.get(User, owner_data["id"])

    def fail_owner_membership(**kwargs):
        raise SQLAlchemyError("simulated owner membership failure")

    monkeypatch.setattr(
        workspace_service,
        "WorkspaceMember",
        fail_owner_membership,
    )

    with pytest.raises(SQLAlchemyError):
        workspace_service.create_workspace(
            db=db_session,
            current_user=owner,
            name="Must Roll Back",
        )

    assert not db_session.in_transaction()
    assert db_session.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.name == "Must Roll Back")
    ) == 0


def test_workspace_refresh_failure_rolls_back_workspace_and_owner(
    db_session,
    user_factory,
    monkeypatch,
):
    owner_data = user_factory("owner")
    owner = db_session.get(User, owner_data["id"])

    def fail_refresh(instance):
        raise SQLAlchemyError("simulated workspace refresh failure")

    monkeypatch.setattr(db_session, "refresh", fail_refresh)

    with pytest.raises(SQLAlchemyError):
        workspace_service.create_workspace(
            db=db_session,
            current_user=owner,
            name="Refresh Must Roll Back",
        )

    assert not db_session.in_transaction()
    assert db_session.scalar(
        select(func.count())
        .select_from(Workspace)
        .where(Workspace.name == "Refresh Must Roll Back")
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(WorkspaceMember)
    ) == 0
