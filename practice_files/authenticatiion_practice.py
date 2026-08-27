from app.database import SessionLocal
from app.services.user_service import authenticate_user


test_cases = [
    ("cocoliquer", "12345678"),
    ("cocoliquer", "wrong_password"),
    ("not_exist", "12345678")
]

with SessionLocal() as db:
    for username, password in test_cases:
        user = authenticate_user(
            db=db,
            username=username,
            password=password
        )

        if user is None:
            print(username, "→ 验证失败")
        else:
            print(username, "→ 验证成功：", user.username)