# Container Deployment

> 中文文档: [README.zh-CN.md](./README.zh-CN.md)

One Docker Compose stack provides three mutually exclusive hardware profiles. Starting one profile stops the other two.

| Profile | Target GPUs | Main model | `MAX_MODEL_LEN` | Main GPU utilization | Memory policy |
| --- | --- | --- | ---: | ---: | --- |
| `regular` | 1 × 32GB main + 3 × 32GB / 3 APIs | BF16 preview | 67,174 | 0.95 | 5 mid-term / 2 long-term blocks |
| `24GB` | 1 × 24GB main + 3 × 24GB / 3 APIs | INT4 AWQ G32 | 81,920 | 0.95 | 5 mid-term / 5 long-term blocks |
| `16GB` | 1 × 16GB main + 3 × 16GB / 3 APIs | INT4 AWQ G32 | 32,768 | 0.95 | 3 mid-term / 1 long-term block |

All profiles use physical GPU capacity, process one frame per second, send at most one frame per request, and cap main-model output at 256 tokens. The `16GB` profile limits each summary to 3,000 tokens. Adjust the regular profile's context length and GPU utilization when the available VRAM differs from 32GB.

## Requirements

- Linux with Docker Engine, Docker Compose, and NVIDIA Container Runtime.
- One main-model GPU and three API GPUs matching the selected profile.
- Free host ports: `7060`, `8065`, `8070`, `8079`, `8099`, and `8991`–`8994`.
- Main, summary, ASR, and TTS model files.

Expected shared model layout:

```text
models/
├── JoyAI-VL-Interaction-Preview/       # regular only
├── Qwen3-VL-4B-Instruct/
├── Qwen3-ASR-1.7B/
└── Qwen3-TTS-12Hz-1.7B-CustomVoice/
```

The `24GB` and `16GB` profiles also require a directory containing `int4_awq_g32/`.

## Quick Start

From the repository root:

```bash
# 1. Pull the official bases and build service images.
docker pull python:3.12-slim-bookworm
docker pull node:22-bookworm-slim
docker pull vllm/vllm-openai:v0.22.0
./container/scripts/build-images.sh

# 2. Create and edit one profile configuration.
cp container/16GB/.env.example container/16GB/.env
${EDITOR:-vi} container/16GB/.env

# 3. Start and verify every endpoint.
./container/manage.sh 16GB up
./container/manage.sh 16GB test
```

Open `https://<host>:8099`. The WebUI uses a generated self-signed certificate.

## Operations

```bash
./container/manage.sh 16GB status
./container/manage.sh 16GB logs
./container/manage.sh 16GB restart
./container/manage.sh 16GB down
```

Replace `16GB` with `regular` or `24GB` to switch profiles.

## Configuration

- `MODEL_ROOT`: shared regular, summary, ASR, and TTS model directory.
- `MAIN_MODEL_ROOT`: directory containing `int4_awq_g32`; required by `24GB` and `16GB`.
- `MAIN_GPU`, `SUMMARY_GPU`, `ASR_GPU`, `TTS_GPU`: GPU assignments; defaults are `0`, `1`, `2`, `3`.
- `CODEX_HOME_HOST`: Codex configuration source; default is `../services/background-agent/codex-home`.
- `BACKGROUND_WORKSPACE_HOST`: writable background-agent workspace.

Relative paths are resolved from `container/docker-compose.yml`. Keep `.env` files local and never commit Codex credentials. Exact image versions are recorded in `images.lock`.

## Appendix: Tuning Memory and Turn Duration

You can edit the active profile's `.env` file to balance memory quality, inference latency, and hardware usage for your deployment:

- `CHUNK`: number of frames in each mid-term memory block. With one frame per turn, the approximate block duration is `CHUNK × LIVE_VLM_PROCESS_INTERVAL` seconds.
- `COMPRESS_EVERY_N_CHUNKS`: number of mid-term blocks accumulated before they are compressed into long-term memory.
- `MID_TERM_MAX_TOKENS` and `MID_TERM_TARGET_TOKEN_COUNT`: maximum and target lengths of each mid-term memory summary.
- `LONG_TERM_MAX_TOKENS` and `LONG_TERM_TARGET_TOKEN_COUNT`: maximum and target lengths of each long-term memory summary.
- `LONG_TERM_MEMORY_WINDOW`: number of long-term memory blocks retained in the context.
- `LIVE_VLM_PROCESS_INTERVAL`: duration of one inference turn in seconds. Increase it to reduce processing frequency and hardware load, or decrease it for faster interaction at a higher compute cost.

Reducing memory lengths, retained block counts, or processing frequency lowers context and compute requirements. Increase them when the available hardware can support longer memory and higher interaction frequency.
