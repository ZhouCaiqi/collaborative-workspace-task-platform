from app.database import SessionLocal
from app.models import Task


db = SessionLocal()

try:
    task = Task(
        title="第一次ORM任务",
        completed=False,
        priority=3
    )

    print("创建对象后：", task.id)

    db.add(task)
    db.commit()
    db.refresh(task)

    print("提交数据库后：", task.id)
    print(task.title, task.completed, task.priority)

finally:
    db.close()