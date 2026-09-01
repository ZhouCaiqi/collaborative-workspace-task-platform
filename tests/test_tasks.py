import pytest


TASK_DATA = {
    "title": "Learn automated testing",
    "completed": False,
    "priority": 3
}

TASKS_DATA = [
    {"title": "Task B", "completed": False, "priority": 2},
    {"title": "Task A", "completed": True, "priority": 3},
    {"title": "Task D", "completed": True, "priority": 1},
    {"title": "Task C", "completed": False, "priority": 3}
]


@pytest.fixture()
def created_task(client, auth_headers):
    response = client.post(
        "/tasks/",
        json=TASK_DATA,
        headers=auth_headers
    )

    assert response.status_code == 201

    return response.json()


@pytest.fixture()
def created_tasks(client, auth_headers):
    tasks = []

    for task_data in TASKS_DATA:
        response = client.post(
            "/tasks/",
            json=task_data,
            headers=auth_headers
        )

        assert response.status_code == 201
        tasks.append(response.json())

    return tasks


def test_create_task(client, auth_headers):
    response = client.post(
        "/tasks/",
        json=TASK_DATA,
        headers=auth_headers
    )

    assert response.status_code == 201

    data = response.json()

    assert isinstance(data["id"], int)
    assert data["title"] == TASK_DATA["title"]
    assert data["completed"] == TASK_DATA["completed"]
    assert data["priority"] == TASK_DATA["priority"]
    assert "owner_id" not in data


def test_get_task(client, auth_headers, created_task):
    task_id = created_task["id"]
    response = client.get(
        f"/tasks/{task_id}",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == created_task


def test_update_task(client, auth_headers, created_task):
    task_id = created_task["id"]
    updated_response = client.patch(
        f"/tasks/{task_id}",
        json={"completed": True},
        headers=auth_headers
    )

    assert updated_response.status_code == 200

    updated_data = updated_response.json()

    assert updated_data["completed"] is True
    assert updated_data["title"] == created_task["title"]
    assert updated_data["priority"] == created_task["priority"]
    assert updated_data["id"] == created_task["id"]


def test_delete_task(client, auth_headers, created_task):
    task_id = created_task["id"]
    delete_response = client.delete(
        f"/tasks/{task_id}",
        headers=auth_headers
    )

    assert delete_response.status_code == 204
    assert delete_response.content == b""

    get_task_response = client.get(
        f"/tasks/{task_id}",
        headers=auth_headers
    )

    assert get_task_response.status_code == 404
    assert get_task_response.json()["code"] == "TASK_NOT_FOUND"
    


def test_cannot_get_other_users_task(client, second_auth_headers, created_task):
    task_id = created_task["id"]
    response = client.get(
        f"/tasks/{task_id}",
        headers=second_auth_headers
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"


def test_cannot_update_other_users_task(client, auth_headers, second_auth_headers, created_task):
    task_id = created_task["id"]
    response = client.patch(
        f"/tasks/{task_id}",
        json={"completed": True},
        headers=second_auth_headers
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"

    owner_response = client.get(
        f"/tasks/{task_id}",
        headers=auth_headers
    )

    assert owner_response.status_code == 200
    assert owner_response.json() == created_task


def test_cannot_delete_other_users_task(client, auth_headers, second_auth_headers, created_task):
    task_id = created_task["id"]
    response = client.delete(
        f"/tasks/{task_id}",
        headers=second_auth_headers
    )

    assert response.status_code == 404
    assert response.json()["code"] == "TASK_NOT_FOUND"

    owner_response = client.get(
        f"/tasks/{task_id}",
        headers=auth_headers
    )

    assert owner_response.status_code == 200
    assert owner_response.json() == created_task


def test_users_only_see_their_own_tasks(client, auth_headers, second_auth_headers):
    first_response = client.post(
        "/tasks/",
        json={
            "title": "First user's task",
            "completed": False,
            "priority": 3
        },
        headers=auth_headers
    )
    second_response = client.post(
        "/tasks/",
        json={
            "title": "Second user's task",
            "completed": True,
            "priority": 2
        },
        headers=second_auth_headers
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    first_data = first_response.json()
    second_data = second_response.json()

    first_get_response = client.get(
        "/tasks/",
        headers=auth_headers
    )
    second_get_response = client.get(
        "/tasks/",
        headers=second_auth_headers
    )

    assert first_get_response.status_code == 200
    assert second_get_response.status_code == 200

    assert first_get_response.json()["total"] == 1
    assert first_get_response.json()["items"] == [first_data]
    assert second_get_response.json()["total"] == 1
    assert second_get_response.json()["items"] == [second_data]


def test_filter_tasks_by_completed(client, auth_headers, created_tasks):
    response = client.get(
        f"/tasks/",
        params={
            "completed": True,
            "sort_by": "id",
            "sort_order": "asc"
        },
        headers=auth_headers
    )

    assert response.json()["total"] == 2
    assert response.json()["items"] == [created_tasks[1], created_tasks[2]]


def test_filter_tasks_by_priority(client, auth_headers, created_tasks):
    response = client.get(
        f"/tasks/",
        params={
            "priority": 3
        },
        headers=auth_headers
    )

    assert response.json()["items"] == [created_tasks[3], created_tasks[1]]


def test_task_pagination(client, auth_headers, created_tasks):
    response = client.get(
        f"/tasks/",
        params={
            "limit": 2,
            "offset": 1,
            "sort_by": "id",
            "sort_order": "asc"
        },
        headers=auth_headers
    )
    data = response.json()

    assert data["total"] == 4
    assert data["limit"] == 2
    assert data["offset"] == 1
    assert data["items"] == created_tasks[1:3]


def test_sort_tasks_by_priority_desc(client, auth_headers, created_tasks):
    response = client.get(
        f"/tasks/",
        params={
            "sort_by": "priority",
            "sort_order": "desc"
        },
        headers=auth_headers
    )

    assert response.json()["items"] == [
        created_tasks[3],  # priority 3，id 较da
        created_tasks[1],  # priority 3，id 较xiao
        created_tasks[0],  # priority 2
        created_tasks[2]   # priority 1
    ]




@pytest.mark.parametrize(
"query_string",
[
    "limit=0",
    "limit=101",
    "offset=-1",
    "priority=6",
    "sort_by=unknown",
    "sort_order=unknown"
]
)
def test_invalid_task_query_params(client, auth_headers, query_string):
    response = client.get(
        f"/tasks/?{query_string}",
        headers=auth_headers
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "task_data",
    [
        {"title": "", "priority": 1},
        {"title": "a" * 101, "priority": 1},
        {"title": "Valid task", "priority": 0},
        {"title": "Valid task", "priority": 6}
    ]
)
def test_create_task_validation(
    client,
    auth_headers,
    task_data
):
    response = client.post(
        "/tasks/",
        json=task_data,
        headers=auth_headers
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "update_data",
    [
        {"title": ""},
        {"priority": 0},
        {"priority": 6}
    ]
)
def test_update_task_validation(
    client,
    auth_headers,
    created_task,
    update_data
):
    response = client.patch(
        f"/tasks/{created_task['id']}",
        json=update_data,
        headers=auth_headers
    )

    assert response.status_code == 422

    get_response = client.get(
        f"/tasks/{created_task['id']}",
        headers=auth_headers
    )

    assert get_response.status_code == 200
    assert get_response.json() == created_task