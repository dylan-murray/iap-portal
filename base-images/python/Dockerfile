# Foundation base image. All other framework base images FROM this.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home /app app

# Shared deps every app has
RUN pip install --no-cache-dir \
        "pyyaml>=6" \
        "httpx>=0.27"

COPY sdk /tmp/iap-sdk
RUN pip install --no-cache-dir /tmp/iap-sdk && rm -rf /tmp/iap-sdk

COPY base-images/iap-app-start /usr/local/bin/iap-app-start
RUN chmod +x /usr/local/bin/iap-app-start

WORKDIR /app
# Numeric so Kubernetes can verify runAsNonRoot.
USER 10001

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/iap-app-start"]
