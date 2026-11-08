# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS wheel-builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=315532800
COPY . /app/
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r /app/requirements-build.lock \
    && python -m pip wheel --no-cache-dir --no-deps --no-build-isolation --wheel-dir /wheel /app

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=315532800
LABEL org.opencontainers.image.source="https://github.com/kladosworld/klados-world"
# .dockerignore remains default-deny; lock files are explicitly admitted.
COPY . /app/
COPY --from=wheel-builder /wheel/ /wheel/
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    chmod -R a+rX /app \
    && python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r /app/requirements-runtime.lock \
    && python -m pip install --no-cache-dir --no-compile --no-deps /wheel/*.whl \
    && python -m pip check \
    && groupadd --gid 10001 klados && useradd --uid 10001 --gid 10001 --no-create-home klados \
    && mkdir -p /data /etc/klados-control && chown 10001:10001 /data /etc/klados-control
USER 10001:10001
WORKDIR /tmp
CMD ["uvicorn", "arena.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS tests
USER root
RUN --mount=type=secret,id=proxy_ca,required=false \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    python -m pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r /app/requirements-dev.lock \
    && python -m pip check
USER 10001:10001
WORKDIR /app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short", "/app/tests"]

FROM base AS runtime
