from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers.users import router as users_router
from app.routers.tasks import router as tasks_router
from app.database import Base, engine
from app.models import Task, User
from app.exceptions import AppException

Base.metadata.create_all(bind=engine)

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