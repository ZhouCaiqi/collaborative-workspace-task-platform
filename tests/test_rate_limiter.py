from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from threading import Event
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select, text
from starlette.requests import Request
import yaml

from app.config import Settings, settings
from app.main import app
from app.models import Task, Workspace, WorkspaceMember
from app.rate_limiter import (
    FIXED_WINDOW_LUA,
    RateLimitDecision,
    RateLimiter,
    get_rate_limiter,
    get_client_ip,
    normalize_login_username,
)
from app.services import user_service, workspace_service


REDIS_IMAGE = "redis:7.4.2-alpine"


def test_compose_redis_is_ephemeral_and_internal_only():
    compose_path = Path(__file__).parents[1] / "compose.yml"
    compose_config = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose_config["services"]
    redis_service = services["redis"]

    assert redis_service["image"] == REDIS_IMAGE
    assert "/data" in redis_service["tmpfs"]
    assert "ports" not in redis_service
    assert "volumes" not in redis_service
    assert redis_service["command"] == [
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "no",
    ]
    assert services["api"]["environment"]["REDIS_URL"] == (
        "redis://redis:6379/0"
    )


class ControlledRateLimiter:
    def __init__(self, *, allowed=True, error=None):
        self.allowed = allowed
        self.error = error
        self.calls = []

    def _result(self, category, kwargs):
        self.calls.append((category, kwargs))
        if self.error is not None:
            raise self.error
        return RateLimitDecision(
            allowed=self.allowed,
            limit=5,
            count=1 if self.allowed else 6,
            remaining=4 if self.allowed else 0,
            retry_after=7,
        )

    def check_login(self, **kwargs):
        return self._result("login", kwargs)

    def check_registration(self, **kwargs):
        return self._result("register", kwargs)

    def check_write(self, **kwargs):
        return self._result("write", kwargs)


class CapturingEvalClient:
    def __init__(self):
        self.keys = []

    def eval(self, script, key_count, key, window_seconds):
        assert script == FIXED_WINDOW_LUA
        assert key_count == 1
        assert int(window_seconds) > 0
        self.keys.append(key)
        return [1, int(window_seconds)]


@dataclass(frozen=True)
class RedisProbe:
    client: Redis
    url: str
    version: str
    container_name: str
    host_port: int


@pytest.fixture(scope="module")
def real_redis_probe():
    container_name = f"task_test_redis_rate_limit_{uuid4().hex}"
    run_command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--publish",
        "127.0.0.1::6379",
        "--tmpfs",
        "/data:rw,noexec,nosuid,size=64m",
        "--health-cmd",
        "redis-cli ping",
        "--health-interval",
        "1s",
        "--health-timeout",
        "1s",
        "--health-retries",
        "30",
        REDIS_IMAGE,
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "no",
    ]

    client = None
    try:
        subprocess.run(
            run_command,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            health = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Health.Status}}",
                    container_name,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            if health == "healthy":
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("temporary Redis did not become healthy")

        port_output = subprocess.run(
            ["docker", "port", container_name, "6379/tcp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        host, raw_port = port_output.splitlines()[0].rsplit(":", 1)
        if host != "127.0.0.1":
            raise RuntimeError("temporary Redis is not loopback-only")

        url = f"redis://127.0.0.1:{int(raw_port)}/15"
        client = Redis.from_url(
            url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=False,
        )
        client.ping()
        client.flushdb()
        version = client.info(section="server")["redis_version"]
        yield RedisProbe(
            client=client,
            url=url,
            version=version,
            container_name=container_name,
            host_port=int(raw_port),
        )
    finally:
        if client is not None:
            client.close()
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )


def _override_limiter(limiter):
    app.dependency_overrides[get_rate_limiter] = lambda: limiter


def _assert_limited_response(response):
    assert response.status_code == 429
    assert response.json() == {
        "code": "RATE_LIMIT_EXCEEDED",
        "message": "Too many requests",
    }
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Limit"] == "5"
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_rate_limit_settings_require_positive_values():
    base = {
        "database_url": "mysql+pymysql://user:password@db/example",
        "secret_key": "test-secret",
        "_env_file": None,
    }
    for field in (
        "rate_limit_login_limit",
        "rate_limit_login_window_seconds",
        "rate_limit_register_limit",
        "rate_limit_register_window_seconds",
        "rate_limit_write_limit",
        "rate_limit_write_window_seconds",
        "redis_connect_timeout_seconds",
        "redis_socket_timeout_seconds",
    ):
        with pytest.raises(ValidationError):
            Settings(**base, **{field: 0})


def test_rate_limit_settings_reject_invalid_url_and_policies():
    base = {
        "database_url": "mysql+pymysql://user:password@db/example",
        "secret_key": "test-secret",
        "_env_file": None,
    }
    for override in (
        {"redis_url": "http://redis:6379/0"},
        {"rate_limit_auth_failure_policy": "open"},
        {"rate_limit_write_failure_policy": "closed"},
    ):
        with pytest.raises(ValidationError):
            Settings(**base, **override)


def test_login_and_registration_keys_are_private_and_isolated():
    client = CapturingEvalClient()
    limiter = RateLimiter(client, "task-api:rate-limit:v1")

    limiter.check_login(client_ip="198.51.100.10", username="Example_User")
    limiter.check_login(client_ip="198.51.100.10", username=" example_user ")
    limiter.check_login(client_ip="198.51.100.10", username="Another_User")
    limiter.check_login(client_ip="198.51.100.11", username="Example_User")
    limiter.check_registration(client_ip="198.51.100.10")
    limiter.check_registration(client_ip="198.51.100.11")

    assert client.keys[0] == client.keys[1]
    assert client.keys[0] != client.keys[2]
    assert client.keys[0] != client.keys[3]
    assert client.keys[4] != client.keys[5]
    joined_keys = " ".join(client.keys)
    assert "Example_User" not in joined_keys
    assert "example_user" not in joined_keys
    assert "198.51.100" not in joined_keys
    assert normalize_login_username(" Example_User ") == "example_user"


def test_client_ip_ignores_untrusted_proxy_headers():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/users/login",
            "headers": [(b"x-forwarded-for", b"203.0.113.99")],
            "client": ("198.51.100.20", 45678),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert get_client_ip(request) == "198.51.100.20"

    request_without_peer = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/users/login",
            "headers": [],
            "client": None,
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    assert get_client_ip(request_without_peer) == "unknown"


def test_write_keys_are_isolated_by_user_scope_and_workspace():
    client = CapturingEvalClient()
    limiter = RateLimiter(client, "task-api:rate-limit:v1")

    limiter.check_write(user_id=1, scope="task.create", workspace_id=10)
    limiter.check_write(user_id=1, scope="task.create", workspace_id=11)
    limiter.check_write(user_id=2, scope="task.create", workspace_id=10)
    limiter.check_write(user_id=1, scope="task.delete", workspace_id=10)
    limiter.check_write(user_id=1, scope="workspace.create", workspace_id=None)

    assert len(set(client.keys)) == 5
    assert client.keys[0].endswith(
        ":write:user:1:scope:task.create:workspace:10"
    )
    assert client.keys[-1].endswith(
        ":write:user:1:scope:workspace.create"
    )


def test_fixed_window_lua_on_real_redis(real_redis_probe):
    real_redis_probe.client.flushdb()
    limiter = RateLimiter(real_redis_probe.client, "task-api:test:rate-limit")
    key = f"task-api:test:algorithm:{uuid4().hex}"

    first = limiter.check_key(key, limit=3, window_seconds=5)
    second = limiter.check_key(key, limit=3, window_seconds=5)
    at_limit = limiter.check_key(key, limit=3, window_seconds=5)
    exceeded = limiter.check_key(key, limit=3, window_seconds=5)

    assert (first.count, first.allowed) == (1, True)
    assert first.retry_after > 0
    assert (second.count, second.allowed) == (2, True)
    assert (at_limit.count, at_limit.allowed) == (3, True)
    assert (exceeded.count, exceeded.allowed) == (4, False)
    assert exceeded.remaining == 0
    assert exceeded.retry_after > 0
    assert real_redis_probe.client.ttl(key) > 0


def test_fixed_window_repairs_missing_ttl(real_redis_probe):
    real_redis_probe.client.flushdb()
    key = f"task-api:test:missing-ttl:{uuid4().hex}"
    real_redis_probe.client.set(key, 1)
    assert real_redis_probe.client.ttl(key) == -1

    decision = RateLimiter(
        real_redis_probe.client,
        "task-api:test:rate-limit",
    ).check_key(key, limit=3, window_seconds=5)

    assert decision.count == 2
    assert decision.allowed is True
    assert real_redis_probe.client.ttl(key) > 0


def test_fixed_window_allows_again_after_expiry(real_redis_probe):
    real_redis_probe.client.flushdb()
    limiter = RateLimiter(real_redis_probe.client, "task-api:test:rate-limit")
    key = f"task-api:test:expiry:{uuid4().hex}"
    assert limiter.check_key(key, limit=1, window_seconds=1).allowed is True
    assert limiter.check_key(key, limit=1, window_seconds=1).allowed is False

    deadline = time.monotonic() + 3
    while real_redis_probe.client.exists(key) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not real_redis_probe.client.exists(key)
    reset = limiter.check_key(key, limit=1, window_seconds=1)
    assert (reset.count, reset.allowed) == (1, True)


def test_fixed_window_concurrency_is_atomic(real_redis_probe):
    real_redis_probe.client.flushdb()
    key = f"task-api:test:concurrency:{uuid4().hex}"
    request_count = 25
    limit = 7
    start = Event()

    def hit_limit():
        client = Redis.from_url(
            real_redis_probe.url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=False,
        )
        try:
            if not start.wait(timeout=5):
                raise TimeoutError("concurrency start event timed out")
            return RateLimiter(client, "task-api:test:rate-limit").check_key(
                key,
                limit=limit,
                window_seconds=30,
            )
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=request_count) as executor:
        futures = [executor.submit(hit_limit) for _ in range(request_count)]
        start.set()
        decisions = [future.result(timeout=10) for future in futures]

    assert sum(decision.allowed for decision in decisions) == limit
    assert sum(not decision.allowed for decision in decisions) == (
        request_count - limit
    )
    assert int(real_redis_probe.client.get(key)) == request_count
    assert real_redis_probe.client.ttl(key) > 0


def test_login_rate_limit_allows_normal_login(
    client,
    registered_user,
):
    limiter = ControlledRateLimiter()
    _override_limiter(limiter)

    response = client.post("/users/login", data=registered_user)

    assert response.status_code == 200
    assert limiter.calls[0][0] == "login"
    assert limiter.calls[0][1]["username"] == registered_user["username"]


def test_login_limit_rejects_before_password_verification(
    client,
    monkeypatch,
):
    limiter = ControlledRateLimiter(allowed=False)
    _override_limiter(limiter)

    def fail_authentication(**_kwargs):
        raise AssertionError("password verification must not run")

    monkeypatch.setattr(user_service, "authenticate_user", fail_authentication)
    response = client.post(
        "/users/login",
        data={"username": "limited_user", "password": "not-verified"},
    )

    _assert_limited_response(response)


def test_login_redis_failure_is_closed_and_logs_no_credentials(
    client,
    monkeypatch,
    caplog,
):
    username = "Sensitive_Login_User"
    password = "sensitive-password-value"
    limiter = ControlledRateLimiter(error=RedisError("connection failed"))
    _override_limiter(limiter)

    def fail_authentication(**_kwargs):
        raise AssertionError("authentication must not run")

    monkeypatch.setattr(user_service, "authenticate_user", fail_authentication)
    with caplog.at_level("WARNING", logger="app.rate_limit"):
        response = client.post(
            "/users/login",
            data={"username": username, "password": password},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "RATE_LIMIT_UNAVAILABLE"
    assert username not in caplog.text
    assert password not in caplog.text
    assert "Authorization" not in caplog.text


def test_invalid_credentials_log_does_not_disclose_username_or_password(
    client,
    registered_user,
    caplog,
):
    limiter = ControlledRateLimiter()
    _override_limiter(limiter)
    wrong_password = "sensitive-wrong-password"

    with caplog.at_level("WARNING", logger="app.security"):
        response = client.post(
            "/users/login",
            data={
                "username": registered_user["username"],
                "password": wrong_password,
            },
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert registered_user["username"] not in caplog.text
    assert wrong_password not in caplog.text


def test_registration_rate_limit_allows_normal_registration(client):
    limiter = ControlledRateLimiter()
    _override_limiter(limiter)

    response = client.post(
        "/users/register",
        json={"username": "rate_register", "password": "password_123"},
    )

    assert response.status_code == 201
    assert limiter.calls[0][0] == "register"


def test_registration_limit_rejects_before_database_work(
    client,
    monkeypatch,
    db_session,
):
    limiter = ControlledRateLimiter(allowed=False)
    _override_limiter(limiter)

    def fail_create(**_kwargs):
        raise AssertionError("user creation must not run")

    monkeypatch.setattr(user_service, "create_user", fail_create)
    response = client.post(
        "/users/register",
        json={"username": "limited_register", "password": "password_123"},
    )

    _assert_limited_response(response)
    assert db_session.is_active is True
    assert db_session.scalar(text("SELECT 1")) == 1


def test_registration_redis_failure_is_closed(client, monkeypatch):
    limiter = ControlledRateLimiter(error=RedisError("timeout"))
    _override_limiter(limiter)

    def fail_create(**_kwargs):
        raise AssertionError("user creation must not run")

    monkeypatch.setattr(user_service, "create_user", fail_create)
    response = client.post(
        "/users/register",
        json={"username": "unavailable_register", "password": "password_123"},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "RATE_LIMIT_UNAVAILABLE"


def test_every_authenticated_write_route_is_limited(
    client,
    user_factory,
    workspace_factory,
    db_session,
):
    owner = user_factory("rate_owner")
    member = user_factory("rate_member")
    add_target = user_factory("rate_target")
    workspace = workspace_factory(owner["headers"], "Rate Limit Matrix")
    add_member = client.post(
        f"/workspaces/{workspace['id']}/members",
        headers=owner["headers"],
        json={"username": member["username"], "role": "MEMBER"},
    )
    assert add_member.status_code == 201
    task = client.post(
        f"/workspaces/{workspace['id']}/tasks",
        headers=owner["headers"],
        json={"title": "Rate-limited task", "priority": 2},
    )
    assert task.status_code == 201

    limiter = ControlledRateLimiter(allowed=False)
    _override_limiter(limiter)
    workspace_count = db_session.scalar(select(func.count()).select_from(Workspace))
    task_count = db_session.scalar(select(func.count()).select_from(Task))

    requests = [
        ("post", "/workspaces", {"name": "Rejected Workspace"}),
        (
            "post",
            f"/workspaces/{workspace['id']}/members",
            {"username": add_target["username"], "role": "MEMBER"},
        ),
        (
            "patch",
            f"/workspaces/{workspace['id']}/members/{member['id']}",
            {"role": "ADMIN"},
        ),
        (
            "delete",
            f"/workspaces/{workspace['id']}/members/{member['id']}",
            None,
        ),
        (
            "post",
            f"/workspaces/{workspace['id']}/tasks",
            {"title": "Rejected Task", "priority": 1},
        ),
        (
            "patch",
            f"/workspaces/{workspace['id']}/tasks/{task.json()['id']}",
            {"title": "Rejected edit"},
        ),
        (
            "delete",
            f"/workspaces/{workspace['id']}/tasks/{task.json()['id']}",
            None,
        ),
        (
            "patch",
            f"/workspaces/{workspace['id']}/tasks/{task.json()['id']}/status",
            {"status": "DONE"},
        ),
        (
            "patch",
            f"/workspaces/{workspace['id']}/tasks/{task.json()['id']}/assignee",
            {"assignee_id": member["id"]},
        ),
    ]
    for method, path, body in requests:
        response = client.request(
            method,
            path,
            headers=owner["headers"],
            json=body,
        )
        _assert_limited_response(response)

    assert [call[1]["scope"] for call in limiter.calls] == [
        "workspace.create",
        "member.add",
        "member.update",
        "member.remove",
        "task.create",
        "task.update",
        "task.delete",
        "task.status",
        "task.assignee",
    ]
    assert limiter.calls[0][1]["workspace_id"] is None
    assert all(
        call[1]["workspace_id"] == workspace["id"]
        for call in limiter.calls[1:]
    )
    assert db_session.scalar(select(func.count()).select_from(Workspace)) == workspace_count
    assert db_session.scalar(select(func.count()).select_from(Task)) == task_count
    assert db_session.get(
        WorkspaceMember,
        (workspace["id"], member["id"]),
    ) is not None
    assert db_session.is_active is True


def test_write_redis_failure_is_open_and_warns_without_token(
    client,
    user_factory,
    caplog,
):
    user = user_factory("fail_open_user")
    limiter = ControlledRateLimiter(error=RedisError("socket timeout"))
    _override_limiter(limiter)
    authorization = user["headers"]["Authorization"]

    with caplog.at_level("WARNING", logger="app.rate_limit"):
        response = client.post(
            "/workspaces",
            headers=user["headers"],
            json={"name": "Fail Open Workspace"},
        )

    assert response.status_code == 201
    assert "policy=open" in caplog.text
    assert authorization not in caplog.text
    assert "socket timeout" not in caplog.text


def test_unauthenticated_write_returns_401_before_rate_limit(client):
    limiter = ControlledRateLimiter(error=AssertionError("must not run"))
    _override_limiter(limiter)

    response = client.post("/workspaces", json={"name": "Unauthorized"})

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOKEN"
    assert limiter.calls == []


def test_non_member_write_returns_hidden_404_before_rate_limit(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("boundary_owner")
    outsider = user_factory("boundary_outsider")
    workspace = workspace_factory(owner["headers"], "Boundary Workspace")
    limiter = ControlledRateLimiter(error=AssertionError("must not run"))
    _override_limiter(limiter)

    response = client.post(
        f"/workspaces/{workspace['id']}/tasks",
        headers=outsider["headers"],
        json={"title": "Hidden", "priority": 1},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "WORKSPACE_NOT_FOUND"
    assert limiter.calls == []


def test_role_denial_happens_before_member_write_rate_limit(
    client,
    user_factory,
    workspace_factory,
):
    owner = user_factory("role_owner")
    member = user_factory("role_member")
    target = user_factory("role_target")
    workspace = workspace_factory(owner["headers"], "Role Boundary")
    response = client.post(
        f"/workspaces/{workspace['id']}/members",
        headers=owner["headers"],
        json={"username": member["username"], "role": "MEMBER"},
    )
    assert response.status_code == 201
    limiter = ControlledRateLimiter(error=AssertionError("must not run"))
    _override_limiter(limiter)

    denied = client.post(
        f"/workspaces/{workspace['id']}/members",
        headers=member["headers"],
        json={"username": target["username"], "role": "MEMBER"},
    )

    assert denied.status_code == 403
    assert denied.json()["code"] == "WORKSPACE_PERMISSION_DENIED"
    assert limiter.calls == []


def test_limit_rejection_does_not_call_workspace_service_or_break_session(
    client,
    auth_headers,
    db_session,
    monkeypatch,
):
    limiter = ControlledRateLimiter(allowed=False)
    _override_limiter(limiter)

    def fail_service(**_kwargs):
        raise AssertionError("workspace service must not run")

    monkeypatch.setattr(workspace_service, "create_workspace", fail_service)
    response = client.post(
        "/workspaces",
        headers=auth_headers,
        json={"name": "Must Not Exist"},
    )

    _assert_limited_response(response)
    assert db_session.is_active is True
    assert db_session.scalar(text("SELECT 1")) == 1


def test_health_does_not_require_rate_limiter(client):
    limiter = ControlledRateLimiter(error=AssertionError("must not run"))
    _override_limiter(limiter)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert limiter.calls == []


def test_openapi_and_get_routes_have_no_rate_limit_dependency(client):
    limiter = ControlledRateLimiter(error=AssertionError("must not run"))
    _override_limiter(limiter)

    openapi = client.get("/openapi.json")
    docs = client.get("/docs")

    assert openapi.status_code == 200
    assert docs.status_code == 200
    assert limiter.calls == []


def test_temporary_redis_uses_expected_version_and_loopback(real_redis_probe):
    assert real_redis_probe.version.startswith("7.4.")
    assert real_redis_probe.host_port > 0
    assert os.path.basename(real_redis_probe.container_name).startswith(
        "task_test_redis_rate_limit_"
    )
