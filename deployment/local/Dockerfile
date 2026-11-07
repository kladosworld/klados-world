# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# .dockerignore is an allowlist; credentials, state and host tooling never enter the build.
COPY . /app/
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    chmod -R a+rX /app && python -m pip install --no-cache-dir /app \
    && groupadd --gid 10001 klados && useradd --uid 10001 --gid 10001 --no-create-home klados \
    && mkdir -p /data /etc/klados-control && chown 10001:10001 /data /etc/klados-control
USER 10001:10001
WORKDIR /tmp
CMD ["uvicorn", "arena.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS tests
USER root
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir '/app[dev]'
USER 10001:10001
WORKDIR /app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "/app/tests"]

FROM base AS runtime
