FROM python:3.11-slim

WORKDIR /app

# 安装可能需要的编译依赖（chromadb / sentence-transformers 某些平台需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Docker 运行时用于持久化 SQLite 和 ChromaDB 的目录
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
