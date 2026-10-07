# One canonical Dockerfile. Edit only if you need extra system packages.
ARG BASE_IMAGE=iap-base/streamlit:local
FROM ${BASE_IMAGE}

COPY . /app
WORKDIR /app

# If you have extra Python deps:
# RUN pip install --no-cache-dir -r requirements.txt

# If you need system packages:
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
