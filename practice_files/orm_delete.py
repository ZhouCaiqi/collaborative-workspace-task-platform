from app.database import SessionLocal
from app.models import Task


db = SessionLocal()

try:
    task = db.get(Task, 1)

    if task is None:
        print("任务不存在")

    else:
        print("准备删除：", task.id, task.title)

        db.delete(task)
        db.commit()

        print("删除成功")

finally:
    db.close()