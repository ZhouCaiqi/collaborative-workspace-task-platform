import pytest, os
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.models  # 确保所有 ORM 模型注册到 Base.metadata
from app.database import Base, get_db
from app.main import app

load_dotenv()

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

# 防止配置错误后误删开发数据库
if make_url(TEST_DATABASE_URL).database != "task_management_test_db":
    raise RuntimeError("Tests must use task_management_test_db")


test_engine = create_engine(
    TEST_DATABASE_URL,
    echo=False
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autoflush=False,
    autocommit=False
)


@pytest.fixture()
def db_session():
    # 每个测试开始前重建空表
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()

        # 每个测试结束后删除测试表
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