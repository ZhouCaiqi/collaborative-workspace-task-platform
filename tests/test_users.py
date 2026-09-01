USER_DATA = {
    "username": "test_user",
    "password": "test_password_123"
}


def test_register_user(client):
    response = client.post(
        "/users/register",
        json=USER_DATA
    )

    assert response.status_code == 201

    data = response.json()

    assert isinstance(data["id"], int)
    assert data["username"] == "test_user"
    assert "password" not in data
    assert "hashed_password" not in data


def test_register_duplicate_username(client):
    first_response = client.post(
        "/users/register",
        json=USER_DATA
    )

    second_response = client.post(
        "/users/register",
        json=USER_DATA
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json() == {
        "code": "USERNAME_ALREADY_EXISTS",
        "message": "Username already registered"
    }