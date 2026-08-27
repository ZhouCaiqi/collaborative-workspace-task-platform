from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import UserCreate
from app.security import hash_password, verify_password
from app.exceptions import UsernameAlreadyExistsError, InvalidCredentialsError


def get_user_by_username(
    db: Session,
    username: str
):
    statement = select(User).where(
        User.username == username
    )
    return db.scalar(statement)


def create_user(
    db: Session,
    user: UserCreate
):
    existing_user = get_user_by_username(
        db=db,
        username=user.username
    )

    if existing_user is not None:
        raise UsernameAlreadyExistsError()

    db_user = User(
        username=user.username,
        hashed_password=hash_password(user.password)
    )

    try:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
    except IntegrityError:
        db.rollback()
        return None
    except SQLAlchemyError:
        db.rollback()
        raise

    return db_user

def authenticate_user(
    db: Session,
    username: str,
    password: str
):
    db_user = get_user_by_username(
        db=db,
        username=username
    )
    if db_user is None:
        raise InvalidCredentialsError()
    verify_result = verify_password(
        plain_password=password,
        hashed_password=db_user.hashed_password
    )
    if verify_result is False:
        raise InvalidCredentialsError()
    return db_user