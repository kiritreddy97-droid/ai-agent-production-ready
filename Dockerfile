# ── Stage 1: build wheels ────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FILE=/app/logs/agent.log \
    TASKS_DB=/app/data/tasks.db
WORKDIR /app

RUN useradd --create-home --uid 1000 agent
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=agent:agent *.py ./
COPY --chown=agent:agent static ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /app/logs /app/data && chown agent:agent /app/logs /app/data
# The entrypoint starts as root only to fix volume ownership, then runs as "agent".

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:%s/health' % os.getenv('PORT','8000')).status==200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
# One process only: the LinkedIn scheduler and task workers live in-process.
CMD ["sh", "-c", "exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
