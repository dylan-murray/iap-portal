ARG BASE=iap-base/python:local
FROM ${BASE}

USER root
RUN pip install --no-cache-dir \
        "flask>=3" \
        "gunicorn>=23"
USER 10001

ENV IAP_APP_FRAMEWORK=flask
