FROM node:20-slim AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home /app app

WORKDIR /app

# Dependencies exactly as locked in uv.lock (hash-checked); see `task deps:export`.
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY backend/pyproject.toml ./
COPY LICENSE ./LICENSE
COPY backend/src ./src
RUN pip install --no-cache-dir --no-deps .

COPY --from=frontend --chmod=644 /frontend/dist ./static
# Directory perms: COPY --chmod only sets files; mark the dirs traversable.
RUN find ./static -type d -exec chmod 755 {} +

# Numeric so Kubernetes can verify runAsNonRoot.
USER 10001

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8090 \
    PORTAL_STATIC_DIR=/app/static

EXPOSE 8090 8091

CMD uvicorn iap_portal_server.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers
