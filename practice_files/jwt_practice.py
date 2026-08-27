import os

from fastapi.testclient import TestClient

from app.main import app
from app.security import create_access_token


client = TestClient(app)
test_username = os.getenv("JWT_TEST_USERNAME", "cocoliquer")
expected_error = {
    "code": "INVALID_TOKEN",
    "message": "Could not validate credentials"
}


def assert_unauthorized(response, case_name: str):
    assert response.status_code == 401
    assert response.json() == expected_error
    assert response.headers.get("www-authenticate") == "Bearer"
    print(
        f"{case_name}：{response.status_code}",
        response.json(),
        f"WWW-Authenticate={response.headers['www-authenticate']}"
    )


response_without_token = client.get("/users/me")
assert_unauthorized(response_without_token, "不带 Token")

response_with_fake_token = client.get(
    "/users/me",
    headers={"Authorization": "Bearer invalid.token.value"}
)
assert_unauthorized(response_with_fake_token, "伪造 Token")

valid_token = create_access_token(test_username)
response_with_valid_token = client.get(
    "/users/me",
    headers={"Authorization": f"Bearer {valid_token}"}
)
assert response_with_valid_token.status_code == 200
assert response_with_valid_token.json()["username"] == test_username
print(
    f"有效 Token：{response_with_valid_token.status_code}",
    response_with_valid_token.json()
)

print("全部 JWT 访问测试通过")
