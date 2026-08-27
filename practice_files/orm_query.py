from app.database import SessionLocal
from app.models import Task
from sqlalchemy import select


db = SessionLocal()

try:
    # task = db.get(Task, 1)

    # if task is None:
    #     print("任务不存在")
    # else:
    #     print("对象类型：", type(task))
    #     print("任务ID：", task.id)
    #     print("标题：", task.title)
    #     print("完成状态：", task.completed)
    #     print("优先级：", task.priority)
    statement = (
        select(Task)
        .where(Task.completed == True)
        .where(Task.priority == 3)
        .limit(10)
    )

    tasks = db.scalars(statement).all()

    print("查询数量：", len(tasks))

    for task in tasks:
        print(
            task.id,
            task.title,
            task.completed,
            task.priority
        )

finally:
    db.close()