# AGENTS.md

This file provides guidance to Codex and other coding agents working in this repository.

## Project overview

This is a Python 3.13 task-management REST API built with FastAPI, Pydantic 2, SQLAlchemy 2, MySQL through PyMySQL, Alembic, Argon2 password hashing, and HS256 JWT bearer authentication.

The repository is initialized with Git. The current application is a learning-oriented, single-owner task backend. Its planned direction is a multi-user collaboration backend, but workspace, membership, role, assignment, comment, and activity-log models are not implemented yet.

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

`pytest.ini` enables branch coverage and requires at least 85% coverage. At the time this guidance was updated, pytest collected 33 tests. There is no configured formatter, linter, type checker, or CI workflow.

## Architecture

The production request path follows router → dependency/service → SQLAlchemy model:

- `app/main.py` creates the FastAPI app, mounts user/task routers, registers exception handlers, and installs request logging middleware. It does not call `Base.metadata.create_all()`.
- `app/routers/users.py` exposes registration, OAuth2 password-form login, and `/users/me`.
- `app/routers/tasks.py` exposes authenticated task CRUD, filtering, offset pagination, and sorting.
- `app/services/user_service.py` handles user lookup, registration, and credential verification.
- `app/services/task_service.py` handles task queries and writes. Every current task operation scopes by `owner_id` for per-user isolation.
- `app/models.py` defines `User` and `Task`; a user has many owned tasks through `Task.owner_id`.
- `app/schemas.py` defines Pydantic request/response models. `TaskUpdate` rejects explicit JSON `null` for title, completed, and priority, while description may be cleared with `null`.
- `app/database.py` owns the engine, declarative base, session factory, and request-scoped `get_db()` dependency.
- `app/security.py` uses pwdlib's recommended password hash and creates 30-minute-by-default HS256 access tokens whose `sub` is currently the username.
- `app/dependencies.py` resolves a bearer token to the current database user.
- `app/exceptions.py` defines domain exceptions. `app/main.py` serializes `AppException` as `{"code": ..., "message": ...}`.
- `app/logging_config.py` configures console logging. Application request logs record method, path, status, and duration; SQLAlchemy engine echo is disabled.

The `/health` endpoint currently reports only process liveness and does not check database readiness.

## Database and transaction behavior

- Schema changes are managed by Alembic. The current linear history creates users/tasks and then adds task descriptions.
- `app.main` does not create tables on import.
- `SessionLocal` uses `autoflush=False` and `expire_on_commit=False`; `get_db()` always closes the request session.
- Current write services commit and refresh ORM objects themselves and roll back on SQLAlchemy failures.
- Task ownership is enforced in service queries with both task ID and owner ID.
- The current transaction-per-service design is adequate for single-record CRUD but will need a use-case-level transaction boundary before adding membership, assignment, and activity-log operations.

When changing models, create and review an Alembic migration. Do not rely on `create_all()` to update an existing database. MySQL DDL is not transactional, so split risky schema changes and data backfills into staged migrations.

## Authentication, authorization, and logs

- Registration hashes passwords before persistence; response schemas do not expose password hashes.
- Login accepts OAuth2 form data and returns a bearer access token.
- Protected task routes resolve the current user and apply owner scoping in the service layer.
- Do not log request bodies, `Authorization` headers, JWTs, passwords, password hashes, database URLs, or secret settings.
- Treat usernames and other account identifiers as potentially sensitive log data.
- Future workspace roles must be checked against database membership; do not trust client-supplied owner/member IDs or embed long-lived authorization state in JWTs.

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
- During the current hardening stage, present the diff and verification results to the user; the user decides whether and when to commit.
