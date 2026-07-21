FROM python:3.12-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL
ARG PIP_TRUSTED_HOST

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/JoyAI-VL-Interaction-main/services/webui/src:/workspace/JoyAI-VL-Interaction-main/services/webinfer

WORKDIR /workspace/JoyAI-VL-Interaction-main

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libglib2.0-0 libgl1 libsm6 libxext6 openssl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.app.txt /tmp/requirements.txt
RUN python -m pip install --prefer-binary --retries 10 -r /tmp/requirements.txt

ENTRYPOINT []
CMD ["/bin/bash"]
