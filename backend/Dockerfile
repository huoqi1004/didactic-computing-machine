# ============================================================
# Agent-Native ERP Backend - Python FastAPI Dockerfile
# ============================================================

FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量, 防止Python生成.pyc文件和启用无缓冲输出
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# 安装系统依赖 (PostgreSQL客户端库、构建工具、curl用于健康检查)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 创建非root用户
RUN groupadd -r erpuser && useradd -r -g erpuser -d /app -s /sbin/nologin erpuser

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 设置文件所有权
RUN chown -R erpuser:erpuser /app

# 切换到非root用户
USER erpuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动 uvicorn 服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
