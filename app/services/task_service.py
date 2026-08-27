from app.schemas import TaskCreate, TaskUpdate
from app.exceptions import TaskNotFoundError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.models import Task



def get_tasks(
        db: Session,
        completed: bool | None,
        limit: int,
        offset: int,
        priority: int | None,
        sort_by: str,
        sort_order: str,
        owner_id: int
):
    sort_columns = {
        "id": Task.id,
        "priority": Task.priority,
        "title": Task.title
    }
    sort_column = sort_columns[sort_by]
    if sort_order == "asc":
        order_expression = sort_column.asc()
        id_order = Task.id.asc()
    else:
        order_expression = sort_column.desc()
        id_order = Task.id.desc()

    conditions = [
        Task.owner_id == owner_id
    ]
    if completed is not None:
        conditions.append(Task.completed == completed)
    if priority is not None:
        conditions.append(Task.priority == priority)

    count_statement = (
        select(func.count())
        .select_from(Task)
        .where(*conditions)
    )
    total = db.scalar(count_statement) or 0

    statement = select(Task).where(*conditions)
    statement = (
        statement
        .order_by(order_expression, id_order)
        .offset(offset)
        .limit(limit)
    )

    items = db.scalars(statement).all()
    return items, total


def get_task_by_id(
        db: Session, 
        task_id: int,
        owner_id: int
):
    statement = select(Task).where(
        Task.id == task_id,
        Task.owner_id == owner_id
    )
    db_task = db.scalar(statement)
    if not db_task:
        raise TaskNotFoundError()
    
    return db_task

def create_task(
    db: Session,
    task: TaskCreate,
    owner_id: int
):
    task_data = task.model_dump()

    db_task = Task(
        **task_data,
        owner_id=owner_id
    )

    try:
        db.add(db_task)
        db.commit()
        db.refresh(db_task)
    except SQLAlchemyError:
        db.rollback()
        raise

    return db_task


def update_task(
        db: Session, 
        task_id: int, 
        task_update: TaskUpdate,
        owner_id: int
):
    statement = select(Task).where(
        Task.id == task_id,
        Task.owner_id == owner_id
    )
    db_task = db.scalar(statement)
    if db_task is None:
        raise TaskNotFoundError()
    update_data = task_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_task, field, value)
    try:
        db.commit()
        db.refresh(db_task)
    except SQLAlchemyError:
        db.rollback()
        raise

    return db_task

def delete_task(
        db: Session,
        task_id: int,
        owner_id: int
):
    statement = select(Task).where(
        Task.id == task_id,
        Task.owner_id == owner_id
    )
    db_task = db.scalar(statement)
    if db_task is None:
        raise TaskNotFoundError()

    try:
        db.delete(db_task)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise

    return True
