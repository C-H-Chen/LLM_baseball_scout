# Dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# 安裝系統套件
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*

# 複製 requirements 並安裝
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案檔案
COPY . .

# 確保 chromadb 資料夾存在
RUN mkdir -p /app/chromadb_wbc_usa

EXPOSE 5000
ENV PORT=5000

# 只用 1 worker 避免多個進程各自載入模型
CMD ["gunicorn", "line_bot:app", "-w", "1", "-k", "sync", "-b", "0.0.0.0:5000", "--timeout", "300"]