FROM python:3.10-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium \
    && python -m playwright install-deps chromium

COPY . .

CMD ["python3", "main.py"]
