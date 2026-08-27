from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token
from app.services import user_service
from app.exceptions import InvalidTokenError


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/users/login", 
    auto_error=False
)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    username = decode_access_token(token)

    if username is None:
        raise InvalidTokenError()

    user = user_service.get_user_by_username(
        db=db,
        username=username
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return user