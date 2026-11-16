# GitHub 赚钱项目雷达 · 生产镜像
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，充分利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 非 root 运行，数据目录可写
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["python", "run.py", "web", "--host", "0.0.0.0", "--port", "8000", "--no-open"]
