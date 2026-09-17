"""Black-box MySQL constraint probe run outside the pytest-cov process."""

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.pool import NullPool


SUCCESS_MARKER = "TASK_CONSTRAINT_PROBE_OK"
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


class ProbeFailure(Exception):
    def __init__(self, category: str, constraint_type: str, code: object):
        super().__init__(category, constraint_type, code)
        self.category = category
        self.constraint_type = constraint_type
        self.code = code


@dataclass(frozen=True)
class ConstraintCase:
    category: str
    constraint_type: str
    overrides: dict[str, object]
    expected_codes: frozenset[int]
    expected_message_marker: str


def _parse_url(value: str | None, setting_name: str):
    if not value:
        raise ProbeFailure("safety", "configuration", f"MISSING_{setting_name}")
    try:
        return make_url(value)
    except (ArgumentError, ValueError):
        raise ProbeFailure(
            "safety",
            "configuration",
            f"INVALID_{setting_name}",
        ) from None


def _validated_test_url():
    development_url = _parse_url(os.getenv("DATABASE_URL"), "DATABASE_URL")
    test_url = _parse_url(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL")
    raw_test_name = test_url.database or ""
    test_name = raw_test_name.casefold()
    development_name = (development_url.database or "").casefold()

    if not test_url.drivername.startswith("mysql"):
        raise ProbeFailure("safety", "configuration", "NON_MYSQL_URL")
    if not test_url.username or test_url.username.casefold() == "root":
        raise ProbeFailure("safety", "configuration", "ROOT_OR_MISSING_USER")
    if test_url == development_url or test_name == development_name:
        raise ProbeFailure("safety", "configuration", "DEVELOPMENT_TARGET")
    if test_name in PROTECTED_DATABASE_NAMES:
        raise ProbeFailure("safety", "configuration", "PROTECTED_DATABASE")
    if (
        raw_test_name != test_name
        or not TEST_DATABASE_NAME_PATTERN.fullmatch(test_name)
    ):
        raise ProbeFailure("safety", "configuration", "UNSAFE_DATABASE_NAME")
    if os.getenv("TEST_DATABASE_RESET_ALLOWED", "").strip().casefold() != "true":
        raise ProbeFailure("safety", "configuration", "RESET_NOT_ALLOWED")
    return test_url


def _mysql_error_code(error: DBAPIError) -> object:
    arguments = getattr(error.orig, "args", ())
    return arguments[0] if arguments else "UNKNOWN"


def _run_case(engine, case: ConstraintCase, values: dict[str, object]) -> None:
    with engine.connect() as connection:
        connection.exec_driver_sql("SET SESSION FOREIGN_KEY_CHECKS = 1")
        foreign_key_checks = connection.scalar(
            text("SELECT @@SESSION.FOREIGN_KEY_CHECKS")
        )
        connection.commit()
        if foreign_key_checks != 1:
            raise ProbeFailure(
                case.category,
                case.constraint_type,
                "FOREIGN_KEY_CHECKS_DISABLED",
            )

        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO tasks "
                    "(id, title, priority, description, workspace_id, "
                    "creator_id, assignee_id, status, created_at, updated_at) "
                    "VALUES (:id, :title, :priority, :description, "
                    ":workspace_id, :creator_id, :assignee_id, :status, "
                    ":created_at, :updated_at)"
                ),
                values | case.overrides,
            )
        except DBAPIError as error:
            transaction.rollback()
            error_code = _mysql_error_code(error)
            if error_code not in case.expected_codes:
                raise ProbeFailure(
                    case.category,
                    case.constraint_type,
                    error_code,
                ) from None
            if case.expected_message_marker.casefold() not in str(error.orig).casefold():
                raise ProbeFailure(
                    case.category,
                    case.constraint_type,
                    error_code,
                ) from None
        else:
            transaction.rollback()
            raise ProbeFailure(
                case.category,
                case.constraint_type,
                "NO_DATABASE_ERROR",
            )

        persisted_count = connection.scalar(
            text("SELECT COUNT(*) FROM tasks WHERE id = :task_id"),
            {"task_id": values["id"]},
        )
        connection.rollback()
        if persisted_count != 0:
            raise ProbeFailure(
                case.category,
                case.constraint_type,
                "ROW_PERSISTED",
            )


def _run_probe() -> None:
    if any(
        variable_name == "COVERAGE_PROCESS_START"
        or variable_name.startswith("COV_CORE_")
        or variable_name.startswith("COVERAGE_")
        for variable_name in os.environ
    ):
        raise ProbeFailure("safety", "coverage", "COVERAGE_ENV_PRESENT")

    test_url = _validated_test_url()
    engine = create_engine(test_url, poolclass=NullPool)

    @event.listens_for(engine, "checkout")
    def _enable_foreign_key_checks(dbapi_connection, *_args) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
        finally:
            cursor.close()

    try:
        repository_root = Path(__file__).parents[2]
        sys.path.insert(0, str(repository_root))
        import app.models  # noqa: F401
        from app.database import Base

        if engine.url != _validated_test_url():
            raise ProbeFailure("safety", "configuration", "TARGET_CHANGED")
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        Base.metadata.create_all(bind=engine)

        with engine.connect() as connection:
            user_result = connection.execute(
                text(
                    "INSERT INTO users (username, hashed_password) "
                    "VALUES ('task_constraint_probe_owner', 'probe-only-hash')"
                )
            )
            user_id = user_result.lastrowid
            workspace_result = connection.execute(
                text(
                    "INSERT INTO workspaces (name, created_by_id) "
                    "VALUES ('Task constraint probe', :user_id)"
                ),
                {"user_id": user_id},
            )
            workspace_id = workspace_result.lastrowid
            connection.execute(
                text(
                    "INSERT INTO workspace_members "
                    "(workspace_id, user_id, role) "
                    "VALUES (:workspace_id, :user_id, 'OWNER')"
                ),
                {"workspace_id": workspace_id, "user_id": user_id},
            )
            connection.commit()

        with engine.connect() as connection:
            parent_counts = connection.execute(
                text(
                    "SELECT "
                    "(SELECT COUNT(*) FROM workspaces WHERE id = :workspace_id), "
                    "(SELECT COUNT(*) FROM users WHERE id = :user_id), "
                    "(SELECT COALESCE(MAX(id), 0) FROM workspaces), "
                    "(SELECT COALESCE(MAX(id), 0) FROM users), "
                    "(SELECT COALESCE(MAX(id), 0) FROM tasks)"
                ),
                {"workspace_id": workspace_id, "user_id": user_id},
            ).one()
            connection.rollback()
        if parent_counts[0] != 1 or parent_counts[1] != 1:
            raise ProbeFailure("setup", "parent_rows", "MISSING_PARENT")

        missing_workspace_id = parent_counts[2] + 1000
        missing_user_id = parent_counts[3] + 1000
        first_task_id = parent_counts[4] + 1000
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cases = (
            ConstraintCase(
                "missing_workspace_id",
                "foreign_key",
                {"workspace_id": missing_workspace_id},
                frozenset({1452}),
                "fk_tasks_workspace_id_workspaces",
            ),
            ConstraintCase(
                "missing_creator_id",
                "foreign_key",
                {"creator_id": missing_user_id},
                frozenset({1452}),
                "fk_tasks_creator_id_users",
            ),
            ConstraintCase(
                "missing_assignee_id",
                "foreign_key",
                {"assignee_id": missing_user_id},
                frozenset({1452}),
                "fk_tasks_assignee_id_users",
            ),
            ConstraintCase(
                "blank_title",
                "check",
                {"title": "   "},
                frozenset({3819, 4025}),
                "ck_tasks_title_length",
            ),
            ConstraintCase(
                "invalid_priority",
                "check",
                {"priority": 0},
                frozenset({3819, 4025}),
                "ck_tasks_priority",
            ),
            ConstraintCase(
                "invalid_status",
                "check",
                {"status": "INVALID"},
                frozenset({3819, 4025}),
                "ck_tasks_status",
            ),
        )

        not_null_columns = (
            "title",
            "priority",
            "workspace_id",
            "creator_id",
            "status",
            "created_at",
            "updated_at",
        )
        cases += tuple(
            ConstraintCase(
                f"null_{column_name}",
                "not_null",
                {column_name: None},
                frozenset({1048}),
                column_name,
            )
            for column_name in not_null_columns
        )

        for offset, case in enumerate(cases):
            values = {
                "id": first_task_id + offset,
                "title": f"constraint probe {offset}",
                "priority": 1,
                "description": None,
                "workspace_id": workspace_id,
                "creator_id": user_id,
                "assignee_id": None,
                "status": "TODO",
                "created_at": now,
                "updated_at": now,
            }
            _run_case(engine, case, values)
    finally:
        engine.dispose()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise ProbeFailure("setup", "arguments", "INVALID_ARGUMENTS")
        _run_probe()
    except ProbeFailure as error:
        print(
            "TASK_CONSTRAINT_PROBE_FAIL "
            f"category={error.category} "
            f"constraint={error.constraint_type} "
            f"code={error.code}",
            file=sys.stderr,
        )
        return 1
    except (TypeError, ValueError):
        print(
            "TASK_CONSTRAINT_PROBE_FAIL "
            "category=setup constraint=arguments code=INVALID_ARGUMENTS",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "TASK_CONSTRAINT_PROBE_FAIL "
            "category=internal constraint=unknown code=UNEXPECTED",
            file=sys.stderr,
        )
        return 2

    print(SUCCESS_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
