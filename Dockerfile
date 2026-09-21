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

# 7. 创建固定 UID/GID 的非 root 运行用户
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin appuser

# 8. 声明端口
EXPOSE 8000

# 9. 使用非 root 用户启动 Uvicorn
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
