# 1. Python 3.13 slim 基础镜像
FROM python:3.13-slim

# 2. 禁止生成 .pyc，让日志立即输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. 设置工作目录
WORKDIR /app

# 4. 先复制依赖文件（利用缓存）
COPY requirements.txt .

# 5. 安装生产依赖
RUN pip install --no-cache-dir -r requirements.txt

# 6. 复制整个项目
COPY . .

# 7. 声明端口
EXPOSE 8000

# 8. 启动 Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]