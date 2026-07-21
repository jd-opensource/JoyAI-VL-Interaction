# web_infer Overview

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

`web_infer` is the real-time video inference service layer for StreamingHarness. It exposes an OpenAI-compatible HTTP API. It does not load models itself; instead, it forwards requests to local vLLM OpenAI API services, while the adapter maintains video frames, chunks, user questions, intermediate summaries, and long-term memory.

In one sentence: the frontend/WebUI sends video frames to `8070`; `live_adapter.py` organizes context and calls the main VLM; the background summary model compresses historical frames into textual memory.

## File Structure

| File | Purpose |
| ---- | ------- |
| `live_adapter.py` | Core aiohttp service; implements the OpenAI-compatible API, session state, chunk memory, main-model forwarding, and output persistence. |
| `memory_summarizer.py` | Summary component; calls summary vLLM to generate chunk-level intermediate summaries and compress multiple summaries into long-term memory. |
| `scripts/run.sh` | Unified entrypoint; centralizes Python and model path configuration, then calls the startup scripts below. |
| `scripts/start_adapter.sh` | Starts the adapter, listening on `127.0.0.1:8070` by default. |
| `scripts/start_all_models.sh` | Starts main-model vLLM services in batch. Model paths and names are passed by `scripts/run.sh` by default. |
| `scripts/start_model.sh` | Starts one main-model vLLM OpenAI API service. |
| `scripts/start_summary_model.sh` | Starts one summary vLLM service; intermediate summaries and long-term memory compression share it. |
| `deploy.md` | Frontend API integration examples, including request/response JSON. |
| `summary_vllm_logs/` | Logs and PID files from `scripts/start_summary_model.sh`. |

## Default Ports and Models

| Service | Port | Default model/path | Notes |
| ------- | ---- | ------------------ | ----- |
| adapter | `8070` | `streaming-infer-adapter` | External OpenAI-compatible API. |
| main model | `7060` | `/tmp/models/JoyAI-VL-Interaction-Preview` | Routed when the request uses `model=JoyAI-VL-Interaction-Preview`. |
| summary model | `8065` | `/tmp/models/Qwen3-VL-4B-Instruct` | Default repo is `Qwen/Qwen3-VL-4B-Instruct`; it handles both intermediate summaries and long-term memory compression. |

Default GPU assignment: summary model uses `0`, main model uses `3`. `scripts/run.sh` first tries to use the shared environment `services/.venv` created by the install script. To specify another environment, pass `VENV_ACTIVATE`, or set `PYTHON_BIN` to choose the Python executable. To force the current shell environment, set `VENV_ACTIVATE=`.

## Quick Start

```bash
# Run from the repository root
cd services/webinfer

# Download models to /tmp/models
../../install/download-models.sh --all

# Start one summary service in the background; logs go to summary_vllm_logs/
bash scripts/run.sh summary

# Start the main model in the foreground
bash scripts/run.sh models

# Start the adapter in the foreground
bash scripts/run.sh adapter
```

You can also let `scripts/run.sh` chain the three startup stages:

```bash
bash scripts/run.sh all
```

Checks:

```bash
curl http://127.0.0.1:8070/health
curl http://127.0.0.1:8070/v1/models
```

OpenAI base URL for the frontend or WebUI:

```text
http://127.0.0.1:8070/v1
```

## External API

`web_infer` only provides an HTTP API. It does not provide WebSocket support and does not process audio.

| Endpoint | Purpose |
| -------- | ------- |
| `GET /health` | Health check; returns the backend list, session count, and summarizer status. |
| `GET /v1/models` | Returns available main models; default is `JoyAI-VL-Interaction-Preview`. |
| `POST /v1/chat/completions` | Core endpoint; compatible with OpenAI chat completions and supports image frames. |
| `POST /v1/streaming/reset` | Resets a session and flushes existing outputs. |

The recommended place for the session is the header:

```http
x-streaming-session: <sessionId>
```

It can also be passed in the body `user` field. Without a session, requests share `default`, which can easily mix state between clients.

Recommended image input format is the OpenAI format:

```json
{
  "type": "image_url",
  "image_url": { "url": "data:image/jpeg;base64,..." }
}
```

`file:///absolute/path.jpg` is also supported, but `ALLOWED_LOCAL_IMAGE_ROOTS` must be configured and the file must be under an allowed directory. See `deploy.md` for a fuller frontend request example.

## Inference and Memory Flow

1. The adapter extracts the session, model, messages, images, and timestamps from the request.
2. If there are no images, it forwards the text request directly to the main model.
3. If images are present, each frame is wrapped as an internal message: `<time range>` plus the image.
4. The prompt is treated as the current user question by default. The question persists until a new prompt replaces it.
5. By default, `FORCE_SILENCE_BEFORE_QUERY=true`: without a user question, the adapter returns `</silence>` directly and does not call the main model.
6. Before calling the main model, the adapter injects `Video History`, `Q&A History`, and the current `User Query`.
7. Main-model output is normalized into one of two formats:
   - `</silence>`
   - `</response> one-sentence reply`
8. Every full `CHUNK` frames, an intermediate summary is generated. Every `COMPRESS_EVERY_N_CHUNKS` intermediate summaries, they are compressed into long-term memory.
9. The response is returned as standard chat completion JSON, with extra `streamingharness.memory/timing/raw_content` fields.

## Key Parameters

| Parameter/environment variable | Default | Description |
| ------------------------------ | ------- | ----------- |
| `VENV_ACTIVATE` | Empty | Optional virtual environment activate script path. |
| `PYTHON_BIN` | `python` | Python executable used to start vLLM and the adapter. |
| `STREAMING_MODEL_REPO` | `jdopensource/JoyAI-VL-Interaction-Preview` | Hugging Face repo used by `../../install/download-models.sh --all`. |
| `MODEL_PATH` | `/tmp/models/JoyAI-VL-Interaction-Preview` | Local path of the main model. |
| `SUMMARY_MODEL_REPO` | `Qwen/Qwen3-VL-4B-Instruct` | Summary model Hugging Face repo used by `../../install/download-models.sh --all`. |
| `SUMMARY_MODEL_PATH` | `/tmp/models/Qwen3-VL-4B-Instruct` | Local path of the summary model; download it first if it does not exist. |
| `ADAPTER_PORT` | `8070` | Adapter listen port. |
| `MAIN_BACKENDS` | See `scripts/start_adapter.sh` | Main-model backend JSON, routed by request `model`. |
| `MAIN_MAX_TOKENS` | Script default `256` | Main-model output length. |
| `MAIN_TEMPERATURE` | `0.8` | Main-model sampling temperature. |
| `CHUNK` | Script default `100` | Number of frames per memory chunk. |
| `COMPRESS_EVERY_N_CHUNKS` | `5` | Number of intermediate summaries to accumulate before compressing into long-term memory. |
| `ASYNC_SUMMARY_LEAD_FRAMES` | Script default `20` | Generate summaries asynchronously ahead of the chunk boundary to reduce waiting. |
| `FRAME_SECONDS` | `1.0` | Estimated frame duration when explicit timestamps are absent. |
| `SUMMARIZER_KEY_FRAMES` | `0` | `0` uses all frames in a chunk for summarization; values greater than 0 sample frames uniformly. |
| `SUMMARIZER_MAX_PIXELS` | `262144` | Maximum input image pixels for summarization. |
| `SUMMARIZER_API_BASE` | `http://127.0.0.1:8065/v1` | Summary model OpenAI API shared by intermediate summaries and long-term memory compression. |
| `SUMMARIZER_MODEL` | `/tmp/models/Qwen3-VL-4B-Instruct` | Model name sent to summary vLLM. |
| `LIVE_SAVE_OUTPUTS` | Script default `true` | Whether to save live output JSON. |
| `FRAME_SAVE_DIR` | `/tmp/streaming_adapter_frames` | Session frame directory; note that data URLs are currently not actually persisted as image files. |
| `ALLOWED_LOCAL_IMAGE_ROOTS` | Same as `FRAME_SAVE_DIR` by default | Root directories allowed for `file://` or local path image references. |

Outputs are written by default to:

```text
result_v2
```

They include full outputs, light outputs, and optional debug input. QA history is kept by default; pass `--no-qa-history` to disable it.

## Stop Services

The recommended path is to use the stop script:

```bash
bash scripts/stop.sh
```

The summary service is a background `nohup` process:

```bash
kill $(cat summary_vllm_logs/vllm_8065.pid)
```

`scripts/start_all_models.sh` and `scripts/start_adapter.sh` are foreground processes and are usually stopped with `Ctrl+C`. After stopping, check the ports:

```bash
ss -ltnp | rg ':(7060|8065|8070)\b'
```

## Common Pitfalls

- The adapter starting successfully does not mean the model is available; a `502` during requests usually means the backend vLLM port is not up.
- Direct browser cross-origin access may fail; use WebUI or a same-origin proxy.
- `/v1/chat/completions` does not implement SSE streaming. It returns regular JSON.
- By default, no query forces silence, which can look like "the model is not talking"; pass a prompt or disable `FORCE_SILENCE_BEFORE_QUERY`.
- `file://` images are restricted by `ALLOWED_LOCAL_IMAGE_ROOTS` by default and are rejected if they are outside the allowlist.
- Intermediate summaries and long-term memory compression share `SUMMARIZER_API_BASE/SUMMARIZER_MODEL`. If the context exceeds the limit, lower `LONG_TERM_MAX_TOKENS/LONG_TERM_TARGET_TOKEN_COUNT` or increase `SUMMARY_MAX_MODEL_LEN`.
- `SUMMARIZER_KEY_FRAMES=0` sends every frame in the chunk to the summary model, which is slower and creates larger requests when chunks are large.
- `scripts/start_summary_model.sh` overwrites the PID file. Before running it again, check the port and any old process.
