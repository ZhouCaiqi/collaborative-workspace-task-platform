from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers.users import router as users_router
from app.routers.tasks import router as tasks_router
from app.database import Base, engine
from app.models import Task, User
from app.exceptions import AppException
import logging
import time
from app.logging_config import setup_logging

Base.metadata.create_all(bind=engine)

setup_logging()
logger = logging.getLogger(__name__)
request_logger = logging.getLogger("app.request")

app = FastAPI(
    title="Task Management API"
)

app.include_router(tasks_router)
app.include_router(users_router)


@app.exception_handler(AppException)
async def app_exception_handler(
    request: Request,
    exc: AppException
):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "code": exc.code,
            "message": exc.message
        }
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request,
    exc: Exception
):
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error"
        }
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000

        request_logger.exception(
            "method=%s path=%s status=500 duration_ms=%.2f",
            request.method,
            request.url.path,
            duration_ms
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000

    request_logger.info(
        "method=%s path=%s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms
    )

    return response