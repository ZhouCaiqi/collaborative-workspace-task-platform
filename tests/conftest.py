import os
import re
import warnings

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
TEST_DATABASE_RESET_ALLOWED = os.getenv("TEST_DATABASE_RESET_ALLOWED")

TEST_DATABASE_NAME_PATTERN = re.compile(r"^[a-z0-9_]+_test_db$")
PROTECTED_DATABASE_NAMES = frozenset({
    "mysql",
    "information_schema",
    "performance_schema",
    "sys",
    "task_management",
    "task_management_db",
    "development",
    "production",
    "staging",
})


def _parse_database_url(value: str | None, setting_name: str):
    if not value:
        raise RuntimeError(f"{setting_name} must be configured before tests run")

    try:
        return make_url(value)
    except (ArgumentError, ValueError):
        raise RuntimeError(
            f"{setting_name} must be a valid SQLAlchemy database URL"
        ) from None


def _validate_test_database_config(
    database_url: str | None,
    test_database_url: str | None,
    reset_allowed: str | None,
):
    development_url = _parse_database_url(database_url, "DATABASE_URL")
    test_url = _parse_database_url(test_database_url, "TEST_DATABASE_URL")

    if not test_url.drivername.startswith("mysql"):
        raise RuntimeError("TEST_DATABASE_URL must use a MySQL driver")

    development_name = (development_url.database or "").casefold()
    raw_test_name = test_url.database or ""
    test_name = raw_test_name.casefold()

    if test_url == development_url:
        raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL")

    if test_name == development_name:
        raise RuntimeError(
            "TEST_DATABASE_URL must not use the development database name"
        )

    if test_name in PROTECTED_DATABASE_NAMES:
        raise RuntimeError("TEST_DATABASE_URL points to a protected database name")

    if (
        raw_test_name != test_name
        or not TEST_DATABASE_NAME_PATTERN.fullmatch(test_name)
    ):
        raise RuntimeError(
            "TEST_DATABASE_URL database name must end with '_test_db' "
            "and contain only lowercase letters, numbers, and underscores"
        )

    if (reset_allowed or "").strip().casefold() != "true":
        raise RuntimeError(
            "TEST_DATABASE_RESET_ALLOWED must be 'true' to confirm that the "
            "test database is disposable"
        )

    return test_url


def _assert_test_database_is_safe():
    return _validate_test_database_config(
        database_url=DATABASE_URL,
        test_database_url=TEST_DATABASE_URL,
        reset_allowed=TEST_DATABASE_RESET_ALLOWED,
    )


validated_test_url = _assert_test_database_is_safe()

if (validated_test_url.username or "").casefold() == "root":
    warnings.warn(
        "TEST_DATABASE_URL uses the MySQL root account; replace it with a "
        "dedicated least-privilege test account",
        RuntimeWarning,
        stacklevel=2,
    )

import app.models  # noqa: E402  # Ensure every ORM model is in Base.metadata.
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

test_engine = create_engine(
    validated_test_url,
    echo=False
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False
)


@pytest.fixture()
def db_session():
    # Revalidate immediately before every destructive schema operation.
    if test_engine.url != _assert_test_database_is_safe():
        raise RuntimeError("The validated test database no longer matches the engine")
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()

        if test_engine.url != _assert_test_database_is_safe():
            raise RuntimeError(
                "The validated test database no longer matches the engine"
            )
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def registered_user(client):
    user_data = {
        "username": "test_user",
        "password": "test_password_123"
    }

    response = client.post(
        "/users/register",
        json=user_data
    )

    assert response.status_code == 201

    return user_data


@pytest.fixture()
def auth_headers(client, registered_user):
    response = client.post(
        "/users/login",
        data={
            "username": registered_user["username"],
            "password": registered_user["password"]
        }
    )

    assert response.status_code == 200

    token = response.json()["access_token"]

    return {
        "Authorization": f"Bearer {token}"
    }


@pytest.fixture()
def second_auth_headers(client):
    user_data = {
        "username": "second_user",
        "password": "second_password_123"
    }

    register_response = client.post(
        "/users/register",
        json=user_data
    )
    assert register_response.status_code == 201

    login_response = client.post(
        "/users/login",
        data=user_data
    )
    assert login_response.status_code == 200

    token = login_response.json()["access_token"]

    return {
        "Authorization": f"Bearer {token}"
    }


@pytest.fixture()
def user_factory(client):
    created_count = 0

    def create_user(prefix="workspace_user"):
        nonlocal created_count
        created_count += 1
        user_data = {
            "username": f"{prefix}_{created_count}",
            "password": "workspace_password_123",
        }

        register_response = client.post(
            "/users/register",
            json=user_data,
        )
        assert register_response.status_code == 201

        login_response = client.post(
            "/users/login",
            data=user_data,
        )
        assert login_response.status_code == 200

        return {
            "id": register_response.json()["id"],
            "username": user_data["username"],
            "headers": {
                "Authorization": (
                    f"Bearer {login_response.json()['access_token']}"
                )
            },
        }

    return create_user


@pytest.fixture()
def workspace_factory(client):
    def create_workspace(headers, name="Collaboration Workspace"):
        response = client.post(
            "/workspaces",
            headers=headers,
            json={"name": name},
        )
        assert response.status_code == 201
        return response.json()

    return create_workspace
