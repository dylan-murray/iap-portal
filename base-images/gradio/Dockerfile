ARG BASE=iap-base/python:local
FROM ${BASE}

USER root
RUN pip install --no-cache-dir "gradio>=4"
USER 10001

ENV IAP_APP_FRAMEWORK=gradio
