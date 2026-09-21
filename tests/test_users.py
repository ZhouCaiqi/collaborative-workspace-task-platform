import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import UsernameAlreadyExistsError
from app.schemas import UserCreate
from app.services import user_service


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


def test_create_user_integrity_error_rolls_back_and_raises_domain_error(
    db_session,
    monkeypatch,
):
    rollback_called = False
    original_rollback = db_session.rollback

    def fail_commit():
        raise IntegrityError(
            "INSERT INTO users",
            {"username": "concurrent_user"},
            Exception("duplicate username"),
        )

    def track_rollback():
        nonlocal rollback_called
        rollback_called = True
        original_rollback()

    monkeypatch.setattr(db_session, "commit", fail_commit)
    monkeypatch.setattr(db_session, "rollback", track_rollback)

    with pytest.raises(UsernameAlreadyExistsError) as exc_info:
        user_service.create_user(
            db=db_session,
            user=UserCreate(
                username="concurrent_user",
                password="concurrent_password_123",
            ),
        )

    assert rollback_called
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "USERNAME_ALREADY_EXISTS"
