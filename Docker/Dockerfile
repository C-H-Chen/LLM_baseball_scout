# Dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# 安裝系統套件
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# 複製 requirements 並安裝
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案檔案
COPY . .

# 確保 chromadb 的資料夾存在
RUN mkdir -p /app/chromadb_wbc_usa

EXPOSE 5000
ENV PORT=5000

# 啟動 line bot
CMD gunicorn line_bot:app -w 1 -b 0.0.0.0:$PORT --timeout 300
