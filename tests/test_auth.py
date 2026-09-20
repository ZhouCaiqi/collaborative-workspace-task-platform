def test_login_success(client, registered_user):
    response = client.post(
        "/users/login",
        data={
            "username": registered_user["username"],
            "password": registered_user["password"]
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data["access_token"], str)
    assert data["access_token"]
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client, registered_user):
    response = client.post(
        "/users/login",
        data={
            "username": registered_user["username"],
            "password": "wrong_password"
        }
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


def test_access_without_token(client):
    response = client.get("/workspaces/1/tasks")

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOKEN"


def test_access_with_invalid_token(client):
    response = client.get(
        "/workspaces/1/tasks",
        headers={"Authorization": "Bearer fake-token"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOKEN"


def test_access_with_valid_token(client, auth_headers, workspace_factory):
    workspace = workspace_factory(auth_headers, "Authentication Workspace")
    response = client.get(
        f"/workspaces/{workspace['id']}/tasks",
        headers=auth_headers
    )

    assert response.status_code == 200
