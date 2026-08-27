from app.database import SessionLocal
from app.models import Task, User


with SessionLocal() as db:
    user = User(
        username="relationship_test_user",
        hashed_password="temporary_test_value"
    )

    task = Task(
        title="测试一对多关系",
        completed=False,
        priority=3,
        owner=user
    )

    db.add(user)
    db.add(task)
    db.commit()

    print("用户ID：", user.id)
    print("任务owner_id：", task.owner_id)
    print("任务所属用户：", task.owner.username)
    print("用户的全部任务：", [item.title for item in user.tasks])