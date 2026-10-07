# GPU workloads. Inherits from nvidia/cuda instead of python-slim.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home /app app

RUN pip install --no-cache-dir \
        "torch>=2.4" \
        "torchvision" \
        "pyyaml>=6" \
        "httpx>=0.27"

COPY sdk /tmp/iap-sdk
RUN pip install --no-cache-dir /tmp/iap-sdk && rm -rf /tmp/iap-sdk

COPY base-images/iap-app-start /usr/local/bin/iap-app-start
RUN chmod +x /usr/local/bin/iap-app-start

WORKDIR /app
USER 10001

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/iap-app-start"]
