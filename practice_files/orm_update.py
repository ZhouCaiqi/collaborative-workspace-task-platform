from app.database import SessionLocal
from app.models import Task


db = SessionLocal()

try:
    task = db.get(Task, 1)

    if task is None:
        print("任务不存在")

    else:
        print(
            "修改前：",
            task.completed,
            task.priority
        )

        task.completed = True
        task.priority = 5

        db.commit()
        db.refresh(task)

        print(
            "修改后：",
            task.completed,
            task.priority
        )

finally:
    db.close()