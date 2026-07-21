# JoyVL Interaction WebUI

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

Real-time vision-language model interaction WebUI. By default, it connects to a local OpenAI-compatible VLM service for local camera or video stream interaction previews.

## Environment Setup

The repository-wide install entrypoint is under `install/`, and the repository-wide runtime entrypoint is `services/scripts/run.sh`. This README only covers single-component WebUI development installation and startup.

Python 3.12 is required.

```bash
# Run from the repository root
cd services/webui
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The default backend address is:

```text
http://127.0.0.1:8070/v1
```

Make sure the corresponding VLM backend service is already running first.

## Start

```bash
source ../.venv/bin/activate
./scripts/start_server.sh
```

Open in the browser:

```text
https://localhost:8099
```

If the browser warns about a self-signed certificate, continue to the site. If certificate files are missing, generate them first:

```bash
./scripts/generate_cert.sh
```

## Common Ports

```bash
# Default script: WebUI 8099, backend 8070
source ../.venv/bin/activate
./scripts/start_server.sh

# WebUI 8090, backend 8070
./scripts/start_server.sh --port 8090 --api-base http://127.0.0.1:8070/v1

# WebUI 8091, backend 8071
./scripts/start_server.sh --port 8091 --api-base http://127.0.0.1:8071/v1
```

## Stop

```bash
./scripts/stop_server.sh
```
