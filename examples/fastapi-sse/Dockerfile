FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8090

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home /app --create-home app

RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30"

COPY sdk /tmp/sdk
RUN pip install --no-cache-dir "/tmp/sdk[fastapi]" && rm -rf /tmp/sdk

WORKDIR /app
USER 10001
COPY --chmod=644 examples/fastapi-sse/app.py examples/fastapi-sse/iap-app.yaml /app/

EXPOSE 8090
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT}
