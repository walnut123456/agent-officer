FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=1601 \
    APP_WORKERS=1

WORKDIR /app

RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY hermes_officer ./hermes_officer
COPY server.py mcp_stdio_server.py ./

EXPOSE 1601
CMD ["uv", "run", "python", "server.py", "--workers", "1"]
