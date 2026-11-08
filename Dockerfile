# BuildQuote · 成本监控工作台（单进程单体）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8010

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY skills ./skills
COPY scripts ./scripts
COPY evals ./evals
COPY .env.example ./.env.example

RUN mkdir -p /app/data

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3)"

CMD ["python", "-m", "app.serve"]
