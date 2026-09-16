# CLAUDE.md

This file provides guidance to Claude Code and other coding assistants working in this repository.

## Project overview

This is a Python 3.13 task-management REST API built with FastAPI, Pydantic 2, SQLAlchemy 2, MySQL through PyMySQL, Alembic, Argon2 password hashing, and HS256 JWT bearer authentication.

The repository is initialized with Git. Workspace collaboration includes workspaces, memberships, and fixed `OWNER`/`ADMIN`/`MEMBER` role checks. The codebase is currently in the Task collaboration 2B compatibility stage: `Task` keeps legacy `owner_id`/`completed` while nullable workspace, creator, assignee, status, and timestamp fields coexist for migration. The public API still uses the original flat `/tasks` routes and legacy schemas/services; nested workspace Task APIs and Task permissions are deferred to 2C.

## Environment

The root `.env` file is ignored by Git. Do not print, commit, copy into images, or expose its values in logs.

Application settings:

- `DATABASE_URL`: production/development SQLAlchemy database URL
- `SECRET_KEY`: secret used to sign JWTs
- `ALGORITHM`: JWT algorithm; defaults to `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES`: access-token lifetime; defaults to 30 minutes

Test and Docker settings used by the current project:

- `TEST_DATABASE_URL`
- `TEST_DATABASE_RESET_ALLOWED`: must be exactly `true` before destructive test setup is allowed
- `MYSQL_ROOT_PASSWORD`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`

Use `.env.example` only as a key/template reference. Never include real credentials in documentation or command output.

## Installation and local commands

From the repository root:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Apply and inspect migrations:

```bash
alembic upgrade head
alembic current
alembic check
```

Run the API locally:

```bash
fastapi dev app/main.py
```

Equivalent Uvicorn command:

```bash
uvicorn app.main:app --reload
```

OpenAPI documentation is available at `/docs`; the liveness endpoint is `/health`.

Run with Docker Compose:

```bash
docker compose up --build
docker compose down
```

The Compose API service waits for MySQL's health check, runs `alembic upgrade head`, and then starts Uvicorn. MySQL data is persisted in the named `mysql_data` volume. `docker compose down` does not delete that volume; do not add `--volumes` unless data deletion is explicitly authorized.

## Tests and database safety

The test suite uses pytest, pytest-cov, FastAPI TestClient, and a real MySQL database configured by `TEST_DATABASE_URL`:

```bash
pytest
```

Important safety behavior:

- `tests/conftest.py` requires a MySQL database whose lowercase name matches `*_test_db`.
- The test URL and database name must differ from `DATABASE_URL`, and protected development/production names are rejected.
- `TEST_DATABASE_RESET_ALLOWED` must be explicitly set to `true` to acknowledge that the target can be destroyed.
- The `db_session` fixture calls `Base.metadata.drop_all()` and `create_all()` before/after tests.
- Therefore, run tests only against a disposable, isolated test MySQL instance with a dedicated least-privilege account.
- Never point `TEST_DATABASE_URL` at a development, staging, or production database, even if its name matches the guard.
- A root test account currently triggers a warning and remains technical debt; do not create or remove MySQL users without explicit authorization.
- Tests currently create tables from ORM metadata rather than by running Alembic, so passing tests do not prove migration correctness.
- `tests/test_task_collaboration_migrations.py` separately exercises the 2B Alembic upgrade/check/downgrade path. It must run only against the same validated disposable MySQL test database and requires a non-root account.

`pytest.ini` enables branch coverage and requires at least 85% coverage. At the time this guidance was updated, pytest collected 79 tests and reported 92.16% coverage, including the original user/task behavior, workspace/membership behavior, 2B ORM configuration, Alembic filtering, migration round trips, and unsafe-downgrade protection. There is no configured formatter, linter, type checker, or CI workflow.

## Architecture

The production request path follows router → dependency/service → SQLAlchemy model:

- `app/main.py` creates the FastAPI app, mounts user, task, workspace, and workspace-member routers, registers exception handlers, and installs request logging middleware. It does not call `Base.metadata.create_all()`.
- `app/routers/users.py` exposes registration, OAuth2 password-form login, and `/users/me`.
- `app/routers/tasks.py` exposes authenticated task CRUD, filtering, offset pagination, and sorting.
- `app/routers/workspaces.py` exposes authenticated workspace creation, membership-scoped detail, and membership-scoped listing.
- `app/routers/workspace_members.py` exposes membership listing plus role-controlled member creation, role updates, and removal.
- `app/services/user_service.py` handles user lookup, registration, and credential verification.
- `app/services/task_service.py` handles task queries and writes. Every current task operation scopes by `owner_id` for per-user isolation.
- `app/services/workspace_service.py` owns the workspace use cases. Workspace creation inserts the workspace and its one `OWNER` membership in a single transaction.
- `app/services/workspace_member_service.py` owns member list/add/update/remove use cases and their transaction boundaries.
- `app/models.py` defines `User`, the 2B-compatible `Task`, `Workspace`, and `WorkspaceMember`. Task temporarily has three explicit User relationships (`owner`, `creator`, and `assignee`) plus its Workspace relationship; legacy and new columns coexist. Membership uses a composite `(workspace_id, user_id)` primary key.
- `app/enums.py` defines shared string enums `MemberRole` and `TaskStatus`; SQLAlchemy persists them as constrained `VARCHAR`, not MySQL native `ENUM`.
- `app/schemas.py` defines Pydantic request/response models. Workspace write schemas forbid extra fields and client-supplied `OWNER`; existing task schemas remain unchanged.
- `app/database.py` owns the engine, declarative base, session factory, and request-scoped `get_db()` dependency.
- `app/security.py` uses pwdlib's recommended password hash and creates 30-minute-by-default HS256 access tokens whose `sub` is currently the username.
- `app/dependencies.py` resolves a bearer token to the current database user, builds a read-only `WorkspaceAccess` from a workspace/membership join, and provides the `require_workspace_roles(...)` dependency factory.
- `app/policies.py` contains pure target-role policy functions; dependencies do not perform target-member authorization.
- `app/exceptions.py` defines domain exceptions. `app/main.py` serializes `AppException` as `{"code": ..., "message": ...}`.
- `app/logging_config.py` configures console logging. Application request logs record method, path, status, and duration; SQLAlchemy engine echo is disabled.

The `/health` endpoint currently reports only process liveness and does not check database readiness.

## Database and transaction behavior

- Schema changes are managed by Alembic. Repository head `c1e8d5a4b762` follows `9f4c2a7b1d30`: Revision 1 adds nullable Task collaboration fields, foreign keys, constraints, indexes, and a migration-only mapping table; Revision 2 creates personal Workspaces and OWNER memberships and backfills legacy Tasks. These 2B revisions have not been applied to the development database and must not be deployed as the final application state.
- `task_collaboration_user_workspace_map` is temporary migration infrastructure, not a business ORM model. `alembic/env.py` excludes only this table from autogenerate comparison. When a later migration removes the table, remove the matching exclusion rule as part of the same change.
- `app.main` does not create tables on import.
- `SessionLocal` uses `autoflush=False` and `expire_on_commit=False`; `get_db()` always closes the request session.
- Routers and dependencies never commit. Each top-level write service owns one use-case transaction, commits once, and rolls back on SQLAlchemy failures.
- Task ownership is enforced in service queries with both task ID and owner ID.
- Workspace access first proves database membership. Missing workspaces and non-members both receive `WORKSPACE_NOT_FOUND`; authenticated members with insufficient roles receive `WORKSPACE_PERMISSION_DENIED`.
- `Workspace.created_by_id` records immutable provenance at the API boundary, while current authority comes from `WorkspaceMember.role`.
- The new nullable Task fields are compatibility data only in 2B. Do not infer workspace authorization for the unchanged `/tasks` routes or change their request/response shape. 2C will make the new fields final, remove `owner_id`/`completed`, and switch to nested workspace Task APIs.

When changing models, create and review an Alembic migration. Do not rely on `create_all()` to update an existing database. MySQL DDL is not transactional, so split risky schema changes and data backfills into staged migrations.

## Authentication, authorization, and logs

- Registration hashes passwords before persistence; response schemas do not expose password hashes.
- Login accepts OAuth2 form data and returns a bearer access token.
- Protected task routes resolve the current user and apply owner scoping in the service layer.
- Do not log request bodies, `Authorization` headers, JWTs, passwords, password hashes, database URLs, or secret settings.
- Treat usernames and other account identifiers as potentially sensitive log data.
- Workspace roles are checked from current database membership on each request; do not trust client-supplied owner/member IDs or embed long-lived authorization state in JWTs.

## Docker behavior

- `Dockerfile` uses Python 3.13 slim, installs `requirements.txt`, copies the application, and starts Uvicorn.
- `compose.yml` defines MySQL and API services, a MySQL health check, startup migration, and the persistent `mysql_data` volume.
- Compose may read the root `.env` for interpolation, but the API service receives only `DATABASE_URL`, `SECRET_KEY`, `ALGORITHM`, and `ACCESS_TOKEN_EXPIRE_MINUTES`. MySQL initialization variables belong only to the database service.
- `.dockerignore` excludes `.env`, Git metadata, virtual environments, caches, coverage output, macOS metadata, practice files, and `CLAUDE.md`.

Do not start/stop containers, remove volumes, rebuild images, or run migrations unless the task authorizes those state changes.

## Repository hygiene

- Preserve existing user changes in a dirty worktree.
- Check `git status` before and after edits.
- Do not commit, switch branches, clean files, alter database state, or run destructive Docker commands unless explicitly requested.
- `.env`, credentials, keys, tokens, database dumps, local volumes, caches, and coverage artifacts must not be committed.
- During the current collaboration-core stage, present the diff and verification results to the user; the user decides whether and when to commit.
