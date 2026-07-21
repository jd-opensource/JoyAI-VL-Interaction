# System Architecture

> 中文文档: [architecture.zh-CN.md](architecture.zh-CN.md)

## Overview

JoyAI-VL-Interaction is a real-time video-language interaction system built around a vision-language interaction model. The system watches a live video stream continuously, decides on its own when to speak, stay silent, or delegate to a background agent, and responds in under a second when needed.

The system is composed of five services arranged in a hub-and-spoke pattern around the WebUI:

```
                          ┌──────────────────┐
                          │   webinfer       │
                          │  (Core VLM API)  │
                          │   :8070          │
                          └────────▲─────────┘
                                   │
┌────────────┐   ┌────────────┐    │    ┌────────────────────┐
│  asr       │   │  tts       │    │    │ background-agent   │
│  :8994     │   │  :8992     │    │    │ :8079              │
└─────▲──────┘   └─────▲──────┘    │    └──────────▲─────────┘
      │                 │           │               │
      └─────────────────┴───────────┼───────────────┘
                                    │
                          ┌─────────┴────────┐
                          │     webui        │
                          │  (Browser + WS)  │
                          │   :8099          │
                          └──────────────────┘
```

## Component Responsibilities

| Service | Directory | Required | Role |
|---------|-----------|----------|------|
| **webinfer** | `services/webinfer` | Yes | Real-time video inference. Exposes an OpenAI-compatible HTTP API. Manages video frames, chunk memory, mid-term summaries, and long-term memory. Forwards requests to local vLLM backends. |
| **webui** | `services/webui` | Yes | Browser frontend and WebRTC server. Handles camera/stream input, renders the interaction UI, and bridges ASR/TTS/background-agent connections. |
| **asr** | `services/asr` | No | Speech recognition adapter. Receives PCM16 audio from WebUI, transcribes via vLLM (Qwen3-ASR), and returns results over WebSocket. |
| **tts** | `services/tts` | No | Speech synthesis adapter. Converts model text replies to PCM16 audio via vLLM-Omni (Qwen3-TTS) and streams back over WebSocket. |
| **background-agent** | `services/background-agent` | No | Background task agent. Handles complex or time-consuming questions delegated by the interaction model, using a code-execution-capable LLM agent. |

## Data Flow

1. **Video input**: WebUI captures webcam frames (or RTSP stream) via WebRTC and sends JPEG frames to `webinfer` at ~1 fps.
2. **Inference**: `webinfer`'s adapter assembles context (current frames, video history, Q&A history, long-term memory) and calls the main VLM via vLLM.
3. **Decision output**: The model returns one of three signals:
   - `</silence>` — nothing to say
   - `</response> text` — proactive or reactive speech
   - `</delegate> task` — hand off to background agent
4. **Memory**: Every N frames form a "chunk"; a summarizer model compresses each chunk into a mid-term summary. Multiple summaries are further compressed into long-term memory.
5. **Speech I/O** (optional): If ASR is running, user speech is transcribed and injected as user queries. If TTS is running, model text responses are synthesized and played back.
6. **Delegation** (optional): When the model delegates, WebUI forwards the task to `background-agent`, which runs it asynchronously and returns results.

## Port Map

| Port | Service | Protocol |
|------|---------|----------|
| 8070 | webinfer (adapter) | HTTP |
| 7060 | webinfer (main VLM vLLM) | HTTP (internal) |
| 8065 | webinfer (summary VLM vLLM) | HTTP (internal) |
| 8099 | webui | HTTPS + WebSocket |
| 8994 | asr (adapter) | HTTP + WebSocket |
| 8993 | asr (vLLM ASR) | HTTP (internal) |
| 8992 | tts (adapter) | HTTP + WebSocket |
| 8991 | tts (vLLM-Omni) | HTTP + WebSocket (internal) |
| 8079 | background-agent | HTTP |

## GPU Allocation (Default)

| GPU | Service | GPU memory utilization |
|-----|---------|------------------------|
| 0 | Main streaming model (vLLM, port 7060) | `0.9` |
| 1 | Summary model (vLLM, port 8065) | `0.9` |
| 2 | ASR model (vLLM, port 8993) | `0.3` |
| 2 | TTS model (vLLM-Omni, port 8991) | `0.6` total deploy budget |

These defaults are set in the respective start scripts. ASR and TTS each default to one-card execution on GPU 2 via `ASR_GPU=2` and `TTS_GPU=2`.

## Runtime Entrypoints

Install and model download scripts live in `install/`. Runtime commands live in `services/`:

When using `services/scripts/run.sh`, treat startup as complete only after the final WebUI process has started.

| Scope | Entrypoint | Purpose |
|-------|------------|---------|
| All services | `services/scripts/run.sh` | Starts minimal or full service sets in the recommended order. |
| Stop services | `services/scripts/stop.sh` | Stops all services or one service group. |
| webinfer | `services/webinfer/scripts/run.sh` | Starts web inference models and adapter. |
| ASR | `services/asr/scripts/run.sh` | Starts ASR model, adapter, or both. |
| TTS | `services/tts/scripts/run.sh` | Starts TTS model, adapter, or both. |
| background-agent | `services/background-agent/scripts/run.sh` | Starts the Codex background agent API. |
| WebUI | `services/webui/scripts/start_server.sh` | Starts the HTTPS WebUI server. |
