from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import UserCreate, UserResponse, TokenResponse
from app.services import user_service

from fastapi.security import OAuth2PasswordRequestForm
from app.security import create_access_token

from app.dependencies import get_current_user
from app.models import User
from app.rate_limiter import (
    enforce_login_rate_limit,
    enforce_registration_rate_limit,
)


router = APIRouter(
    prefix="/users",
    tags=["users"]
)


@router.post("/register", status_code=201, response_model=UserResponse)
def create_user(
    user: UserCreate,
    _rate_limit: None = Depends(enforce_registration_rate_limit),
    db: Session = Depends(get_db)
):
    return  user_service.create_user(
        db=db,
        user=user
    )


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(
        enforce_login_rate_limit
    ),
    db: Session = Depends(get_db)
):
    db_user = user_service.authenticate_user(
        db=db,
        username=form_data.username,
        password=form_data.password
    )
    token = create_access_token(db_user.username)
    return {
        "access_token": token,
        "token_type": "bearer"
    }


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(get_current_user)
):
    return current_user
