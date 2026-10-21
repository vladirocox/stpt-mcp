# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

RUN pip wheel --no-deps --wheel-dir /dist .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STPT_MCP_TRANSPORT=http \
    STPT_MCP_HOST=0.0.0.0 \
    STPT_MCP_PORT=8000 \
    STPT_MCP_ALLOWED_HOSTS=localhost:8000,127.0.0.1:8000

RUN groupadd --system --gid 10001 stpt \
    && useradd --system --uid 10001 --gid stpt --no-create-home --shell /usr/sbin/nologin stpt

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl \
    && rm -f /tmp/*.whl

USER stpt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

ENTRYPOINT ["stpt-mcp", "serve"]
