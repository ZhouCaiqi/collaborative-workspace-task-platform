from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa

from conftest import _assert_test_database_is_safe, test_engine


BASELINE_REVISION = "6c2f9a4d7e31"
STRUCTURE_REVISION = "9f4c2a7b1d30"
BACKFILL_REVISION = "c1e8d5a4b762"
FINAL_REVISION = "d4b6e8f1a203"
MAPPING_TABLE = "task_collaboration_user_workspace_map"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _drop_all_test_tables() -> None:
    if test_engine.url != _assert_test_database_is_safe():
        raise RuntimeError("The migration test engine is no longer safe")
    metadata = sa.MetaData()
    metadata.reflect(bind=test_engine)
    metadata.drop_all(bind=test_engine)


@pytest.fixture()
def migration_config():
    safe_url = _assert_test_database_is_safe()
    if (safe_url.username or "").casefold() == "root":
        pytest.fail(
            "Migration tests require a dedicated non-root MySQL account"
        )

    _drop_all_test_tables()
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "alembic"),
    )
    config.attributes["database_url"] = safe_url.render_as_string(
        hide_password=False
    )

    try:
        yield config
    finally:
        _drop_all_test_tables()


def _seed_legacy_database() -> dict:
    with test_engine.begin() as connection:
        user_ids = []
        for username in (
            "migration_user_one",
            "migration_user_two",
            "migration_user_without_tasks",
        ):
            result = connection.execute(
                sa.text(
                    "INSERT INTO users (username, hashed_password) "
                    "VALUES (:username, :hashed_password)"
                ),
                {
                    "username": username,
                    "hashed_password": "migration-test-hash",
                },
            )
            user_ids.append(result.lastrowid)

        task_specs = (
            ("First incomplete task", False, 1, user_ids[0]),
            ("First completed task", True, 2, user_ids[0]),
            ("Second incomplete task", False, 3, user_ids[1]),
        )
        task_ids = []
        for title, completed, priority, owner_id in task_specs:
            result = connection.execute(
                sa.text(
                    "INSERT INTO tasks "
                    "(title, completed, priority, owner_id, description) "
                    "VALUES "
                    "(:title, :completed, :priority, :owner_id, NULL)"
                ),
                {
                    "title": title,
                    "completed": completed,
                    "priority": priority,
                    "owner_id": owner_id,
                },
            )
            task_ids.append(result.lastrowid)

        workspace_result = connection.execute(
            sa.text(
                "INSERT INTO workspaces (name, created_by_id) "
                "VALUES (:name, :created_by_id)"
            ),
            {
                "name": "Personal Workspace - migration_user_one",
                "created_by_id": user_ids[0],
            },
        )
        existing_workspace_id = workspace_result.lastrowid
        connection.execute(
            sa.text(
                "INSERT INTO workspace_members "
                "(workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, 'OWNER')"
            ),
            {
                "workspace_id": existing_workspace_id,
                "user_id": user_ids[0],
            },
        )

        existing_workspace = connection.execute(
            sa.text(
                "SELECT id, name, created_by_id, created_at, updated_at "
                "FROM workspaces WHERE id = :workspace_id"
            ),
            {"workspace_id": existing_workspace_id},
        ).one()
        existing_membership = connection.execute(
            sa.text(
                "SELECT workspace_id, user_id, role, joined_at, updated_at "
                "FROM workspace_members "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": existing_workspace_id},
        ).one()

    return {
        "user_ids": user_ids,
        "task_ids": task_ids,
        "existing_workspace_id": existing_workspace_id,
        "existing_workspace": tuple(existing_workspace),
        "existing_membership": tuple(existing_membership),
    }


def _assert_revision(revision: str) -> None:
    with test_engine.connect() as connection:
        assert connection.scalar(
            sa.text("SELECT version_num FROM alembic_version")
        ) == revision


def _assert_structure_revision() -> None:
    inspector = sa.inspect(test_engine)
    task_columns = {
        column["name"]: column
        for column in inspector.get_columns("tasks")
    }
    for column_name in (
        "workspace_id",
        "creator_id",
        "assignee_id",
        "status",
        "created_at",
        "updated_at",
    ):
        assert task_columns[column_name]["nullable"] is True
    assert task_columns["status"]["type"].length == 16
    assert task_columns["created_at"]["type"].fsp == 6
    assert task_columns["updated_at"]["type"].fsp == 6

    foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("tasks")
    }
    expected_foreign_keys = {
        "fk_tasks_workspace_id_workspaces": "workspaces",
        "fk_tasks_creator_id_users": "users",
        "fk_tasks_assignee_id_users": "users",
    }
    for name, referred_table in expected_foreign_keys.items():
        assert foreign_keys[name]["referred_table"] == referred_table
        assert foreign_keys[name]["options"]["ondelete"] == "RESTRICT"

    checks = {
        check["name"]: check["sqltext"]
        for check in inspector.get_check_constraints("tasks")
    }
    assert "ck_tasks_status" in checks
    for status in ("TODO", "IN_PROGRESS", "DONE"):
        assert status in checks["ck_tasks_status"]

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("tasks")
    }
    assert indexes["ix_tasks_workspace_id_id"] == ("workspace_id", "id")
    assert indexes["ix_tasks_workspace_status_id"] == (
        "workspace_id",
        "status",
        "id",
    )
    assert indexes["ix_tasks_workspace_assignee_id"] == (
        "workspace_id",
        "assignee_id",
        "id",
    )
    assert indexes["ix_tasks_creator_id"] == ("creator_id",)
    assert indexes["ix_tasks_assignee_id"] == ("assignee_id",)

    mapping_columns = {
        column["name"]: column
        for column in inspector.get_columns(MAPPING_TABLE)
    }
    assert set(mapping_columns) == {
        "user_id",
        "workspace_id",
        "legacy_task_count",
        "legacy_task_max_id",
        "migrated_at",
    }
    assert mapping_columns["legacy_task_count"]["nullable"] is False
    assert mapping_columns["migrated_at"]["nullable"] is False
    assert mapping_columns["migrated_at"]["type"].fsp == 6

    mapping_foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys(MAPPING_TABLE)
    }
    assert {
        name: foreign_key["options"]["ondelete"]
        for name, foreign_key in mapping_foreign_keys.items()
    } == {
        "fk_task_collaboration_map_user_id_users": "RESTRICT",
        "fk_task_collaboration_map_workspace_id_workspaces": "RESTRICT",
    }
    unique_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(MAPPING_TABLE)
    }
    assert unique_constraints["uq_task_collaboration_map_workspace_id"] == (
        "workspace_id",
    )


def _assert_backfilled(seed: dict) -> list[dict]:
    with test_engine.connect() as connection:
        mappings = [
            dict(row)
            for row in connection.execute(
                sa.text(
                    f"SELECT user_id, workspace_id, legacy_task_count, "
                    f"legacy_task_max_id, migrated_at FROM {MAPPING_TABLE} "
                    "ORDER BY user_id"
                )
            ).mappings().all()
        ]
        assert [row["user_id"] for row in mappings] == seed["user_ids"]
        assert [row["legacy_task_count"] for row in mappings] == [2, 1, 0]
        assert [row["legacy_task_max_id"] for row in mappings] == [
            seed["task_ids"][1],
            seed["task_ids"][2],
            None,
        ]

        generated_workspace_ids = [row["workspace_id"] for row in mappings]
        generated_workspaces = connection.execute(
            sa.text(
                "SELECT id, created_by_id FROM workspaces "
                "WHERE id IN :workspace_ids ORDER BY id"
            ).bindparams(
                sa.bindparam("workspace_ids", expanding=True)
            ),
            {"workspace_ids": generated_workspace_ids},
        ).all()
        assert len(generated_workspaces) == 3
        assert {
            row.created_by_id for row in generated_workspaces
        } == set(seed["user_ids"])

        owners = connection.execute(
            sa.text(
                "SELECT workspace_id, user_id, role FROM workspace_members "
                "WHERE workspace_id IN :workspace_ids "
                "ORDER BY workspace_id"
            ).bindparams(
                sa.bindparam("workspace_ids", expanding=True)
            ),
            {"workspace_ids": generated_workspace_ids},
        ).all()
        assert len(owners) == 3
        assert all(row.role == "OWNER" for row in owners)

        task_rows = connection.execute(
            sa.text(
                "SELECT id, completed, owner_id, workspace_id, creator_id, "
                "assignee_id, status, created_at, updated_at "
                "FROM tasks ORDER BY id"
            )
        ).mappings().all()
        mapping_by_user = {row["user_id"]: row for row in mappings}
        assert len(task_rows) == 3
        for task in task_rows:
            mapping = mapping_by_user[task["owner_id"]]
            assert task["workspace_id"] == mapping["workspace_id"]
            assert task["creator_id"] == task["owner_id"]
            assert task["assignee_id"] is None
            assert task["status"] == (
                "DONE" if task["completed"] else "TODO"
            )
            assert task["created_at"] == mapping["migrated_at"]
            assert task["updated_at"] == mapping["migrated_at"]

        existing_workspace = connection.execute(
            sa.text(
                "SELECT id, name, created_by_id, created_at, updated_at "
                "FROM workspaces WHERE id = :workspace_id"
            ),
            {"workspace_id": seed["existing_workspace_id"]},
        ).one()
        existing_membership = connection.execute(
            sa.text(
                "SELECT workspace_id, user_id, role, joined_at, updated_at "
                "FROM workspace_members "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": seed["existing_workspace_id"]},
        ).one()
        assert tuple(existing_workspace) == seed["existing_workspace"]
        assert tuple(existing_membership) == seed["existing_membership"]

        duplicate_name_count = connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM workspaces "
                "WHERE name = 'Personal Workspace - migration_user_one'"
            )
        )
        assert duplicate_name_count == 2

    return mappings


def _assert_finalized(seed: dict) -> None:
    inspector = sa.inspect(test_engine)
    task_columns = {
        column["name"]: column
        for column in inspector.get_columns("tasks")
    }
    assert set(task_columns) == {
        "id",
        "title",
        "description",
        "priority",
        "workspace_id",
        "creator_id",
        "assignee_id",
        "status",
        "created_at",
        "updated_at",
    }
    for column_name in (
        "workspace_id",
        "creator_id",
        "status",
        "created_at",
        "updated_at",
    ):
        assert task_columns[column_name]["nullable"] is False
    assert task_columns["assignee_id"]["nullable"] is True
    assert "TODO" in str(task_columns["status"]["default"])
    assert "utc_timestamp(6)" in str(
        task_columns["created_at"]["default"]
    ).casefold()
    assert "utc_timestamp(6)" in str(
        task_columns["updated_at"]["default"]
    ).casefold()

    checks = {
        check["name"]: check["sqltext"]
        for check in inspector.get_check_constraints("tasks")
    }
    assert {
        "ck_tasks_title_length",
        "ck_tasks_priority",
        "ck_tasks_status",
    } <= set(checks)

    foreign_keys = {
        foreign_key["name"]: foreign_key
        for foreign_key in inspector.get_foreign_keys("tasks")
    }
    assert set(foreign_keys) == {
        "fk_tasks_workspace_id_workspaces",
        "fk_tasks_creator_id_users",
        "fk_tasks_assignee_id_users",
    }
    assert all(
        foreign_key["options"]["ondelete"] == "RESTRICT"
        for foreign_key in foreign_keys.values()
    )

    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("tasks")
    }
    assert indexes == {
        "ix_tasks_workspace_id_id": ("workspace_id", "id"),
        "ix_tasks_workspace_status_id": (
            "workspace_id",
            "status",
            "id",
        ),
        "ix_tasks_workspace_assignee_id": (
            "workspace_id",
            "assignee_id",
            "id",
        ),
        "ix_tasks_creator_id": ("creator_id",),
        "ix_tasks_assignee_id": ("assignee_id",),
    }
    assert MAPPING_TABLE in inspector.get_table_names()

    with test_engine.connect() as connection:
        task_rows = connection.execute(
            sa.text(
                "SELECT id, workspace_id, creator_id, assignee_id, status, "
                "created_at, updated_at FROM tasks ORDER BY id"
            )
        ).mappings().all()
        assert [row["id"] for row in task_rows] == seed["task_ids"]
        assert [row["creator_id"] for row in task_rows] == [
            seed["user_ids"][0],
            seed["user_ids"][0],
            seed["user_ids"][1],
        ]
        assert [row["status"] for row in task_rows] == [
            "TODO",
            "DONE",
            "TODO",
        ]
        assert all(row["workspace_id"] is not None for row in task_rows)
        assert all(row["assignee_id"] is None for row in task_rows)
        assert all(row["created_at"] is not None for row in task_rows)
        assert all(row["updated_at"] is not None for row in task_rows)

        existing_workspace = connection.execute(
            sa.text(
                "SELECT id, name, created_by_id, created_at, updated_at "
                "FROM workspaces WHERE id = :workspace_id"
            ),
            {"workspace_id": seed["existing_workspace_id"]},
        ).one()
        existing_membership = connection.execute(
            sa.text(
                "SELECT workspace_id, user_id, role, joined_at, updated_at "
                "FROM workspace_members "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": seed["existing_workspace_id"]},
        ).one()
        assert tuple(existing_workspace) == seed["existing_workspace"]
        assert tuple(existing_membership) == seed["existing_membership"]


def test_task_collaboration_migrations_round_trip_and_repeat(
    migration_config,
):
    command.upgrade(migration_config, BASELINE_REVISION)
    seed = _seed_legacy_database()

    command.upgrade(migration_config, STRUCTURE_REVISION)
    _assert_revision(STRUCTURE_REVISION)
    _assert_structure_revision()

    command.upgrade(migration_config, BACKFILL_REVISION)
    _assert_revision(BACKFILL_REVISION)
    first_mappings = _assert_backfilled(seed)

    command.upgrade(migration_config, FINAL_REVISION)
    _assert_revision(FINAL_REVISION)
    _assert_finalized(seed)
    command.check(migration_config)

    with test_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE tasks SET status = 'IN_PROGRESS' WHERE id = :id"),
            {"id": seed["task_ids"][0]},
        )

    command.downgrade(migration_config, BACKFILL_REVISION)
    _assert_revision(BACKFILL_REVISION)
    with test_engine.connect() as connection:
        downgraded_rows = connection.execute(
            sa.text(
                "SELECT id, owner_id, completed, status "
                "FROM tasks ORDER BY id"
            )
        ).mappings().all()
        assert [row["owner_id"] for row in downgraded_rows] == [
            seed["user_ids"][0],
            seed["user_ids"][0],
            seed["user_ids"][1],
        ]
        assert [row["completed"] for row in downgraded_rows] == [
            False,
            True,
            False,
        ]
        assert downgraded_rows[0]["status"] == "IN_PROGRESS"

    # Revision 2's stricter downgrade guard expects its original TODO mapping.
    with test_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE tasks SET status = 'TODO' WHERE id = :id"),
            {"id": seed["task_ids"][0]},
        )
    _assert_backfilled(seed)

    command.downgrade(migration_config, STRUCTURE_REVISION)
    _assert_revision(STRUCTURE_REVISION)
    with test_engine.connect() as connection:
        assert connection.scalar(
            sa.text(f"SELECT COUNT(*) FROM {MAPPING_TABLE}")
        ) == 0
        generated_workspace_ids = [
            row["workspace_id"] for row in first_mappings
        ]
        assert connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM workspaces WHERE id IN :workspace_ids"
            ).bindparams(
                sa.bindparam("workspace_ids", expanding=True)
            ),
            {"workspace_ids": generated_workspace_ids},
        ) == 0
        assert connection.scalar(
            sa.text(
                "SELECT COUNT(*) FROM workspaces WHERE id = :workspace_id"
            ),
            {"workspace_id": seed["existing_workspace_id"]},
        ) == 1
        restored_tasks = connection.execute(
            sa.text(
                "SELECT completed, owner_id, workspace_id, creator_id, "
                "assignee_id, status, created_at, updated_at "
                "FROM tasks ORDER BY id"
            )
        ).mappings().all()
        assert [row["completed"] for row in restored_tasks] == [
            False,
            True,
            False,
        ]
        assert [row["owner_id"] for row in restored_tasks] == [
            seed["user_ids"][0],
            seed["user_ids"][0],
            seed["user_ids"][1],
        ]
        for task in restored_tasks:
            for column_name in (
                "workspace_id",
                "creator_id",
                "assignee_id",
                "status",
                "created_at",
                "updated_at",
            ):
                assert task[column_name] is None

    command.downgrade(migration_config, BASELINE_REVISION)
    _assert_revision(BASELINE_REVISION)
    inspector = sa.inspect(test_engine)
    assert MAPPING_TABLE not in inspector.get_table_names()
    assert {
        column["name"] for column in inspector.get_columns("tasks")
    } == {
        "id",
        "title",
        "completed",
        "priority",
        "owner_id",
        "description",
    }

    command.upgrade(migration_config, FINAL_REVISION)
    _assert_revision(FINAL_REVISION)
    _assert_finalized(seed)
    command.check(migration_config)


def test_final_revision_validation_stops_before_ddl(migration_config):
    command.upgrade(migration_config, BASELINE_REVISION)
    seed = _seed_legacy_database()
    command.upgrade(migration_config, BACKFILL_REVISION)

    with test_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE tasks SET priority = 0 WHERE id = :id"),
            {"id": seed["task_ids"][0]},
        )

    with pytest.raises(
        RuntimeError,
        match="does not satisfy final constraints",
    ):
        command.upgrade(migration_config, FINAL_REVISION)

    _assert_revision(BACKFILL_REVISION)
    inspector = sa.inspect(test_engine)
    task_columns = {
        column["name"]: column
        for column in inspector.get_columns("tasks")
    }
    assert "owner_id" in task_columns
    assert "completed" in task_columns
    assert task_columns["workspace_id"]["nullable"] is True
    check_names = {
        check["name"]
        for check in inspector.get_check_constraints("tasks")
    }
    assert "ck_tasks_title_length" not in check_names
    assert "ck_tasks_priority" not in check_names


def test_backfill_downgrade_rejects_changed_generated_workspace_atomically(
    migration_config,
):
    command.upgrade(migration_config, BASELINE_REVISION)
    seed = _seed_legacy_database()
    command.upgrade(migration_config, BACKFILL_REVISION)
    mappings = _assert_backfilled(seed)
    target_mapping = mappings[0]

    with test_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO workspace_members "
                "(workspace_id, user_id, role) "
                "VALUES (:workspace_id, :user_id, 'MEMBER')"
            ),
            {
                "workspace_id": target_mapping["workspace_id"],
                "user_id": seed["user_ids"][1],
            },
        )

    with test_engine.connect() as connection:
        counts_before = (
            connection.scalar(sa.text("SELECT COUNT(*) FROM workspaces")),
            connection.scalar(
                sa.text("SELECT COUNT(*) FROM workspace_members")
            ),
            connection.scalar(sa.text("SELECT COUNT(*) FROM tasks")),
            connection.scalar(
                sa.text(f"SELECT COUNT(*) FROM {MAPPING_TABLE}")
            ),
        )

    with pytest.raises(
        RuntimeError,
        match="unexpected members",
    ):
        command.downgrade(migration_config, STRUCTURE_REVISION)

    _assert_revision(BACKFILL_REVISION)
    with test_engine.connect() as connection:
        counts_after = (
            connection.scalar(sa.text("SELECT COUNT(*) FROM workspaces")),
            connection.scalar(
                sa.text("SELECT COUNT(*) FROM workspace_members")
            ),
            connection.scalar(sa.text("SELECT COUNT(*) FROM tasks")),
            connection.scalar(
                sa.text(f"SELECT COUNT(*) FROM {MAPPING_TABLE}")
            ),
        )
    assert counts_after == counts_before
