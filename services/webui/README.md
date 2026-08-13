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
https://localhost:7099
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

Remote access requires exposing the WebUI port and the LiveKit media port:

| Purpose | Protocol and default port | Expose externally |
| --- | --- | --- |
| WebUI HTTPS and proxied LiveKit signaling | TCP `7099` | Yes |
| WebRTC media | UDP `8299` | Yes |
| WebRTC TCP fallback | TCP `8299` | Recommended |
| Internal LiveKit signaling | TCP `8298` | No; listens on `127.0.0.1` only |

The WebUI proxies LiveKit signaling through the `/livekit` path, so `TCP 8298` does not need to be exposed separately. If you change the WebUI port with `--port` or the media ports with `LIVEKIT_UDP_PORT` / `LIVEKIT_TCP_PORT`, expose the resulting ports in the firewall or cloud security group. By default `LIVEKIT_TCP_PORT` matches `LIVEKIT_UDP_PORT`, both on `8299`.

These are inbound port requirements. The first automatic LiveKit Server download also requires outbound HTTPS access to GitHub Releases, but no additional inbound port is needed.

## Uploaded Video Input

The WebUI can upload a video file to `/tmp/joyvl-video-uploads` without starting analysis. After upload, press Start to play the local file in the browser. Frames sampled from the picture actually displayed by the browser are sent through the existing WebUI connection on port `7099`, published into the local LiveKit room, and consumed by the server-side VLM worker. This avoids requiring remote browsers to reach the LiveKit RTC media port while preserving the LiveKit processing path. Uploaded-video playback does not launch ffmpeg or create an RTSP stream, so the analyzer cannot run ahead of the displayed picture. Session cleanup stops the relay, LiveKit worker, and in-flight VLM requests. External RTSP input continues to use the separate RTSP pipeline.

This mode expects an RTSP server such as MediaMTX to already be listening on the configured RTSP base URL.

## Common Ports

```bash
# Default script: WebUI 7099, backend 8070
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
