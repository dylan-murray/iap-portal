ARG BASE=iap-base/python:local
FROM ${BASE}

USER root
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "gunicorn>=23"
USER 10001

ENV IAP_APP_FRAMEWORK=fastapi
