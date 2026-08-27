from app.security import hash_password, verify_password


password = "test123456"

hashed_1 = hash_password(password)
hashed_2 = hash_password(password)

print("哈希1：", hashed_1)
print("哈希2：", hashed_2)
print("两次哈希是否相同：", hashed_1 == hashed_2)
print("正确密码：", verify_password(password, hashed_1))
print("错误密码：", verify_password("wrong", hashed_1))