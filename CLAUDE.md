# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

This is a Python task-management REST API built with FastAPI, SQLAlchemy 2.x, Pydantic 2, MySQL (via PyMySQL), and JWT bearer authentication. It is currently a learning project and is not initialized as a Git repository.

## Environment and commands

The application requires these variables in the root `.env` file:

- `DATABASE_URL`: SQLAlchemy database URL
- `SECRET_KEY`: secret used to sign HS256 JWTs

Install and run the project from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
fastapi dev app/main.py
```

The equivalent direct Uvicorn command is:

```bash
uvicorn app.main:app --reload
```

Once running, OpenAPI documentation is available at `/docs`.

There is currently no test suite, test configuration, formatter, linter, type checker, build step, or migration tool configured. Consequently, there is no project-specific command for running all tests or a single test. Do not claim one exists; if tests are later added, document their actual commands here.

## Architecture

The production application follows a router → service → SQLAlchemy model structure:

- `app/main.py` constructs the FastAPI app, mounts the users and tasks routers, registers the shared `AppException` handler, and calls `Base.metadata.create_all()` at import time.
- `app/routers/` defines HTTP concerns and injects database sessions/current-user authentication. Routers delegate database behavior to services.
- `app/services/` contains user authentication and task CRUD/query logic. Task service operations always include `owner_id`, which enforces per-user task isolation.
- `app/models.py` defines the `User` and `Task` ORM models. They have a one-to-many relationship through `Task.owner_id`.
- `app/schemas.py` contains Pydantic request/response models. `TaskUpdate` is a partial-update schema but explicitly rejects JSON `null` for supplied fields.
- `app/database.py` owns the SQLAlchemy engine, session factory, declarative base, and request-scoped `get_db()` dependency.
- `app/security.py` handles Argon2 password hashing and 30-minute HS256 access tokens. The username is stored in the JWT `sub` claim.
- `app/dependencies.py` resolves bearer tokens to the current database user. Protected task endpoints use this dependency.
- `app/exceptions.py` defines domain exceptions. `app/main.py` converts them to JSON shaped as `{"code": ..., "message": ...}`.

User routes provide registration, OAuth2 password-form login, and the current-user endpoint. Task routes provide authenticated CRUD plus filtering, pagination, and sorting.

## Database behavior

Importing `app.main` creates missing tables directly with `Base.metadata.create_all()`. There is no Alembic migration history, so ORM schema changes are not automatically applied to existing tables.

Database write services commit and refresh ORM objects, and roll back on SQLAlchemy failures. The session uses `expire_on_commit=False`. SQL logging is enabled with `engine` option `echo=True`.

## Practice scripts

`jwt_practice.py` and files under `practice_files/` are standalone learning scripts, not part of the production request path. Several scripts create, update, or delete real records using the configured database. Do not run them merely to inspect or validate the application; first determine their side effects and obtain confirmation when those effects are not explicitly requested.
