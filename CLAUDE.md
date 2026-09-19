# CLAUDE.md

This file provides guidance to Claude Code and other coding assistants working in this repository.

## Project overview

This is a Python 3.13 task-management REST API built with FastAPI, Pydantic 2, SQLAlchemy 2, MySQL through PyMySQL, Alembic, Redis through synchronous redis-py, Argon2 password hashing, and HS256 JWT bearer authentication.

The repository is initialized with Git. Workspace collaboration includes workspaces, memberships, fixed `OWNER`/`ADMIN`/`MEMBER` role checks, the final collaborative Task model, dedicated status and assignee workflows, member self-claim/release, and assignment cleanup when a member is removed. Tasks are workspace-scoped and use creator, optional assignee, status, and timestamp fields; the legacy `owner_id`/`completed` model and flat `/tasks` routes have been removed from application code.

## Environment

The root `.env` file is ignored by Git. Do not print, commit, copy into images, or expose its values in logs.

Application settings:

- `DATABASE_URL`: production/development SQLAlchemy database URL
- `SECRET_KEY`: secret used to sign JWTs
- `ALGORITHM`: JWT algorithm; defaults to `HS256`
- `ACCESS_TOKEN_EXPIRE_MINUTES`: access-token lifetime; defaults to 30 minutes
- `REDIS_URL`: synchronous redis-py connection URL; never print it because it may contain credentials
- `REDIS_CONNECT_TIMEOUT_SECONDS` / `REDIS_SOCKET_TIMEOUT_SECONDS`: positive Redis connection/read timeouts
- `RATE_LIMIT_KEY_PREFIX`: namespaced, versioned Redis key prefix
- `RATE_LIMIT_LOGIN_LIMIT` / `RATE_LIMIT_LOGIN_WINDOW_SECONDS`: login fixed-window policy
- `RATE_LIMIT_REGISTER_LIMIT` / `RATE_LIMIT_REGISTER_WINDOW_SECONDS`: registration fixed-window policy
- `RATE_LIMIT_WRITE_LIMIT` / `RATE_LIMIT_WRITE_WINDOW_SECONDS`: authenticated-write fixed-window policy
- `RATE_LIMIT_AUTH_FAILURE_POLICY`: locked to `closed`
- `RATE_LIMIT_WRITE_FAILURE_POLICY`: locked to `open`

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
- `tests/test_task_collaboration_migrations.py` separately exercises the complete Task collaboration upgrade/check/downgrade path through Revision 3. It must run only against the same validated disposable MySQL test database and requires a non-root account.
- The test engine uses `NullPool`, disposes connections around schema resets, and explicitly enables MySQL foreign-key checks on every checkout so constraint assertions are deterministic.
- Destructive Alembic round-trip tests are collected last; they repeatedly rebuild the shared disposable schema and clean it when complete.
- `tests/test_rate_limiter.py` starts an exact, loopback-only Redis container with tmpfs and no volume. It validates the Lua script against real Redis, including strict concurrent accounting, and removes the exact container in fixture cleanup. Ordinary API tests inject a permissive limiter and never connect to development Redis.

`pytest.ini` enables branch coverage and requires at least 85% coverage. At the time this guidance was updated, pytest collected 187 tests and reported approximately 94% application coverage. Coverage includes users/authentication, Redis rate-limit algorithms and failure policy, workspace/membership RBAC, nested Task CRUD and isolation, status/assignment permissions and idempotency, real-MySQL and real-Redis concurrency, member-removal cleanup, transaction rollback behavior, final ORM constraints, migration round trips, and unsafe-downgrade protection. There is no configured formatter, linter, type checker, or CI workflow.

## Architecture

The production request path follows router → dependency/service → SQLAlchemy model:

- `app/main.py` creates the FastAPI app, mounts user, task, workspace, and workspace-member routers, registers exception handlers, and installs request logging middleware. It does not call `Base.metadata.create_all()`.
- `app/routers/users.py` exposes registration, OAuth2 password-form login, and `/users/me`.
- `app/routers/tasks.py` exposes workspace-nested Task CRUD at `/workspaces/{workspace_id}/tasks`, dedicated `/{task_id}/status` and `/{task_id}/assignee` PATCH operations, filtering, offset pagination, and stable sorting. The old flat `/tasks` API no longer exists.
- `app/routers/workspaces.py` exposes authenticated workspace creation, membership-scoped detail, and membership-scoped listing.
- `app/routers/workspace_members.py` exposes membership listing plus role-controlled member creation, role updates, and removal.
- `app/services/user_service.py` handles user lookup, registration, and credential verification.
- `app/services/task_service.py` handles Task queries and writes using `workspace_id` as the isolation boundary. Creation derives workspace and creator from trusted access context; general updates use explicit fields and role/creator checks. Status and assignee workflows lock current membership rows before the Task row, re-read protected state, and own their transaction through commit or rollback.
- `app/services/workspace_service.py` owns the workspace use cases. Workspace creation inserts the workspace and its one `OWNER` membership in a single transaction.
- `app/services/workspace_member_service.py` owns member list/add/update/remove use cases and their transaction boundaries. Removal locks the target membership and atomically clears that user's unfinished Task assignments with one bulk update before deleting the membership; DONE assignments remain as history.
- `app/models.py` defines `User`, final `Task`, `Workspace`, and `WorkspaceMember`. Task has required workspace/creator/status/timestamps, optional assignee, explicit creator/assignee foreign-key relationships, and no legacy owner/completed columns. Membership uses a composite `(workspace_id, user_id)` primary key.
- `app/enums.py` defines shared string enums `MemberRole` and `TaskStatus`; SQLAlchemy persists them as constrained `VARCHAR`, not MySQL native `ENUM`.
- `app/schemas.py` defines Pydantic request/response models. Workspace and Task write schemas forbid extra fields. Task creation cannot accept server-controlled ownership, assignment, status, or timestamp fields; general Task updates permit only title, description, and priority. `TaskStatusUpdate` and `TaskAssigneeUpdate` isolate workflow-controlled fields on dedicated endpoints.
- `app/database.py` owns the engine, declarative base, session factory, and request-scoped `get_db()` dependency.
- `app/rate_limiter.py` owns the synchronous redis-py connection pool, fixed-window Lua script, digest-based key construction, login/registration dependencies, and fail-open authenticated-write enforcement. Routers never construct Redis clients.
- `app/security.py` uses pwdlib's recommended password hash and creates 30-minute-by-default HS256 access tokens whose `sub` is currently the username.
- `app/dependencies.py` resolves a bearer token to the current database user, builds read-only `WorkspaceAccess` and `WorkspaceTaskAccess` contexts, and provides the `require_workspace_roles(...)` dependency factory. Workspace membership is proven before a task lookup, preventing cross-workspace resource enumeration.
- `app/policies.py` contains pure membership-role, Task edit/delete, status-transition, and self-assignment policy functions; dependencies establish resource boundaries while services map policy outcomes to stable domain errors.
- `app/exceptions.py` defines domain exceptions. `app/main.py` serializes `AppException` as `{"code": ..., "message": ...}`.
- `app/logging_config.py` configures console logging. Application request logs record method, path, status, and duration; SQLAlchemy engine echo is disabled.

The `/health` endpoint currently reports only process liveness and does not check database readiness.

## Database and transaction behavior

- Schema changes are managed by Alembic. Repository head `d4b6e8f1a203` follows `c1e8d5a4b762` and `9f4c2a7b1d30`: Revision 1 adds nullable compatibility fields and migration infrastructure; Revision 2 creates personal Workspaces/OWNER memberships and backfills legacy Tasks; Revision 3 validates the backfill, makes final fields non-null, adds final defaults/checks, and removes `owner_id`/`completed`.
- The development database has been accepted at repository head `d4b6e8f1a203`. This Redis batch has no schema changes and must not generate or apply an Alembic revision.
- `task_collaboration_user_workspace_map` remains temporary migration infrastructure, not a business ORM model. `alembic/env.py` excludes only this table from autogenerate comparison. It has not yet been removed; cleanup remains deferred to the separate 5D-1 batch because Revision 2 downgrade depends on it.
- `app.main` does not create tables on import.
- `SessionLocal` uses `autoflush=False` and `expire_on_commit=False`; `get_db()` always closes the request session.
- Routers and dependencies never commit. Each top-level write service owns one use-case transaction, commits once, and rolls back on SQLAlchemy failures. Status/assignee operations also explicitly roll back domain errors after acquiring row locks, and perform no database I/O after commit.
- Task list queries are always scoped to `workspace_id`. Detail, update, and delete first prove workspace membership and then query by both task ID and workspace ID.
- Workspace access first proves database membership. Missing workspaces and non-members both receive `WORKSPACE_NOT_FOUND`; authenticated members with insufficient roles receive `WORKSPACE_PERMISSION_DENIED`.
- `Workspace.created_by_id` records immutable provenance at the API boundary, while current authority comes from `WorkspaceMember.role`.
- OWNER and ADMIN can edit/delete any Task in their Workspace and can move Tasks between any statuses. MEMBER can edit/delete only Tasks they created, but status authority depends on assignment: an assigned MEMBER may advance only `TODO -> IN_PROGRESS -> DONE`.
- Status and assignee remain excluded from general PATCH. OWNER/ADMIN can assign, reassign, or clear unfinished Tasks to current members. MEMBER can claim an unassigned unfinished Task or release their own; DONE assignments are frozen unless an authorized manager first reopens the Task. Exact-value requests are idempotent and do not change `updated_at`.

When changing models, create and review an Alembic migration. Do not rely on `create_all()` to update an existing database. MySQL DDL is not transactional, so split risky schema changes and data backfills into staged migrations.

## Authentication, authorization, and logs

- Registration hashes passwords before persistence; response schemas do not expose password hashes.
- Login accepts OAuth2 form data and returns a bearer access token.
- Login uses an IP-digest plus normalized-username-digest key and defaults to 5 requests per 60 seconds. Registration uses an IP-digest key and defaults to 3 requests per hour. Both fail closed with `RATE_LIMIT_UNAVAILABLE` when Redis cannot execute the check.
- Authenticated writes use a separate fixed-window bucket for each current `user_id`, operation scope, and `workspace_id` when applicable; each bucket defaults to 60 requests per 60 seconds, rather than all Task writes sharing one aggregate bucket. They fail open with a structured warning during Redis outages so existing authenticated work can continue.
- Versioned key formats are `task-api:rate-limit:v1:login:ip:<sha256>:username:<sha256>`, `...:register:ip:<sha256>`, and `...:write:user:<id>:scope:<scope>[:workspace:<id>]`. SHA-256 digests keep raw IP addresses and usernames out of Redis keys and rate-limit logs; the stable digests are pseudonymous identifiers, not irreversible anonymization.
- Limit exhaustion returns `RATE_LIMIT_EXCEEDED` with HTTP 429 and a positive `Retry-After`. The fixed-window Lua script atomically increments, sets or repairs TTL, and returns count/TTL. Fixed windows can permit a boundary burst across adjacent windows; sliding-window/token-bucket designs are intentionally deferred.
- Client identity comes only from `request.client.host`. `X-Forwarded-For` and `X-Real-IP` are ignored because no trusted-proxy deployment boundary exists yet.
- Protected Task routes resolve current membership, scope every Task to the path Workspace, and apply role/creator policy in the service layer.
- Do not log request bodies, `Authorization` headers, JWTs, passwords, password hashes, database URLs, or secret settings.
- Treat usernames and other account identifiers as potentially sensitive log data.
- Workspace roles are checked from current database membership on each request; do not trust client-supplied owner/member IDs or embed long-lived authorization state in JWTs.

## Docker behavior

- `Dockerfile` uses Python 3.13 slim, installs `requirements.txt`, copies the application, and starts Uvicorn.
- `compose.yml` defines MySQL, Redis, and API services, health checks, startup migration, and the persistent `mysql_data` volume.
- Redis uses the explicit `redis:7.4.2-alpine` image, is reachable only on the internal Compose network, and runs with RDB/AOF disabled. Its `/data` path is explicitly overlaid with tmpfs rather than a persistent Docker volume, so rate-limit windows are intentionally ephemeral and clear when the Redis container stops or is recreated.
- API startup waits for MySQL but does not require Redis to be healthy. The Redis pool connects lazily; request-level fail-open/fail-closed policy handles outages, and application shutdown disconnects the pool.
- Compose may read the root `.env` for interpolation, but the API service receives only its listed database, JWT, Redis, and rate-limit settings. MySQL initialization variables belong only to the database service.
- `.dockerignore` excludes `.env`, Git metadata, virtual environments, caches, coverage output, macOS metadata, practice files, and `CLAUDE.md`.

Do not start/stop containers, remove volumes, rebuild images, or run migrations unless the task authorizes those state changes.

## Repository hygiene

- Preserve existing user changes in a dirty worktree.
- Check `git status` before and after edits.
- Do not commit, switch branches, clean files, alter database state, or run destructive Docker commands unless explicitly requested.
- `.env`, credentials, keys, tokens, database dumps, local volumes, caches, and coverage artifacts must not be committed.
- During the current collaboration-core stage, present the diff and verification results to the user; the user decides whether and when to commit.
