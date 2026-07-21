# JoyVL Interaction WebUI

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

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
https://localhost:8199
```

If the browser warns about a self-signed certificate, continue to the site. If certificate files are missing, generate them first:

```bash
./scripts/generate_cert.sh
```

## LiveKit Server and Network Ports

The WebUI uses **LiveKit Server 1.13.2** by default. On the first start, if `services/webui/.livekit/livekit-server` is missing, the startup script automatically downloads the binary for the current Linux architecture from LiveKit's GitHub Releases, verifies its SHA-256 checksum, and starts it. `x86_64/amd64` and `aarch64/arm64` are supported.

Override the default version with the `LIVEKIT_VERSION` environment variable:

```bash
LIVEKIT_VERSION=1.13.2 ./scripts/start_server.sh
```

`.livekit/` is a generated local runtime directory containing the downloaded binary, configuration, and logs. It should not be committed to Git.

Remote access requires exposing only **one TCP port and one UDP port**:

| Purpose | Protocol and default port | Expose externally |
| --- | --- | --- |
| WebUI HTTPS and proxied LiveKit signaling | TCP `8199` | Yes |
| WebRTC media | UDP `8299` | Yes |
| Internal LiveKit signaling | TCP `8298` | No; listens on `127.0.0.1` only |

The WebUI proxies LiveKit signaling through the `/livekit` path, so `TCP 8298` does not need to be exposed separately. If you change the WebUI port with `--port` or the media port with `LIVEKIT_UDP_PORT`, expose the resulting single TCP port and single UDP port in the firewall or cloud security group.

These are inbound port requirements. The first automatic LiveKit Server download also requires outbound HTTPS access to GitHub Releases, but no additional inbound port is needed.

## Common Ports

```bash
# Default script: WebUI 8199, backend 8070
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
