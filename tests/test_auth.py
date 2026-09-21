from app.security import create_access_token


INVALID_TOKEN_BODY = {
    "code": "INVALID_TOKEN",
    "message": "Could not validate credentials",
}


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
    assert response.json() == INVALID_TOKEN_BODY
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_access_with_invalid_token(client):
    response = client.get(
        "/workspaces/1/tasks",
        headers={"Authorization": "Bearer fake-token"}
    )

    assert response.status_code == 401
    assert response.json() == INVALID_TOKEN_BODY
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_access_with_valid_token_for_missing_user_returns_invalid_token(client):
    missing_username = "missing_auth_user"
    token = create_access_token(missing_username)

    response = client.get(
        "/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json() == INVALID_TOKEN_BODY
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert missing_username not in response.text


def test_access_with_valid_token(client, auth_headers, workspace_factory):
    workspace = workspace_factory(auth_headers, "Authentication Workspace")
    response = client.get(
        f"/workspaces/{workspace['id']}/tasks",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert "WWW-Authenticate" not in response.headers


def test_non_auth_domain_error_does_not_include_bearer_challenge(
    client,
    auth_headers,
):
    response = client.get(
        "/workspaces/999999999/tasks",
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "WORKSPACE_NOT_FOUND",
        "message": "Workspace not found",
    }
    assert "WWW-Authenticate" not in response.headers
