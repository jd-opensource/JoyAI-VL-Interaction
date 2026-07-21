# JoyVL TTS Adapter Service

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

This directory provides the minimal TTS adapter service used by `services/webui`. It converts the current WebUI TTS WebSocket protocol into the vLLM Omni Qwen3-TTS Speech WebSocket protocol.

The WebUI side uses a raw `pcm16` audio stream. The adapter requests `response_format: "pcm"` from vLLM Omni and forwards the upstream PCM bytes directly to WebUI for playback.

## Installation

Run the following commands from the repository root.

```bash
uv venv -p python3.12 --seed services/tts/.venv
source services/tts/.venv/bin/activate
uv pip install -c install/constraints.txt \
    vllm==0.22.0 vllm-omni==0.22.0 \
    --torch-backend=auto
uv pip install -c install/constraints.txt -e "services/tts[dev]"
```

Notes: this runtime is pinned to Python 3.12, `vllm-omni==0.22.0`, and `vllm==0.22.0`. The major versions of vLLM Omni and vLLM must match. If `vllm==0.23.0` is installed with the current `vllm-omni==0.22.0`, startup fails because of entrypoint compatibility issues.

Before first use, download models to the unified `/tmp/models` directory:

```bash
./install/download-models.sh --all
```

## Start vLLM Omni

The recommended path is to use the component startup script:

```bash
./services/tts/scripts/run.sh model
```

First locate the Qwen3-TTS deploy config bundled with vLLM Omni:

```bash
python - <<'PY'
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec("vllm_omni")
if spec is None or not spec.submodule_search_locations:
    raise SystemExit("missing package: vllm_omni")
path = Path(next(iter(spec.submodule_search_locations))).resolve() / "deploy" / "qwen3_tts.yaml"
if not path.is_file():
    raise SystemExit(f"missing deploy config: {path}")
print(path)
PY
```

Then start vLLM Omni on port `8991`. The component script uses physical GPU 2 by default and loads `services/tts/config/qwen3_tts_lowmem.yaml`, a single-card deploy config for short interactive replies. It caps the text/talker stage at 1024 tokens, keeps the code2wav stage at 8192 codec tokens, and uses `gpu_memory_utilization=0.3` for each of the two TTS stages, for a total TTS budget of `0.6`.

The equivalent manual command is:

```bash
CUDA_VISIBLE_DEVICES=2 vllm-omni serve \
    /tmp/models/Qwen3-TTS-12Hz-1.7B-CustomVoice \
    --served-model-name Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice \
    --deploy-config services/tts/config/qwen3_tts_lowmem.yaml \
    --omni \
    --port 8991 \
    --tensor-parallel-size 1 \
    --trust-remote-code \
    --enforce-eager
```

Set `TTS_LOW_MEMORY_CONFIG=0` to use the vLLM Omni bundled `qwen3_tts.yaml`, or set `TTS_DEPLOY_CONFIG=/path/to/config.yaml` to provide a custom deploy file.

## Start the Adapter Service

The recommended path is to use the component startup script. It waits for the TTS vLLM Omni port to become available, retrying every 5 seconds by default, and then starts the adapter. After `/health` is ready, it automatically runs one short-text warmup in the background through the full `adapter -> vLLM Omni -> audio` path, spending cold-start costs such as Triton JIT, CUDA graph capture, and code predictor warmup before user traffic arrives:

```bash
./services/tts/scripts/run.sh adapter
```

The default warmup synthesizes a short greeting and writes output to `/tmp/joyvl_tts_warmup.pcm`. To start only the adapter manually, run:

```bash
joyvl-tts-adapter --host 0.0.0.0 --port 8992
```

You can also start the model and adapter together through the unified entrypoint:

```bash
./services/tts/scripts/run.sh all
```

Health check:

```bash
curl http://127.0.0.1:8992/health
```

The TTS address used by WebUI by default is:

```text
ws://127.0.0.1:8992/ws/tts
```

## Tests

Unit tests that do not require a real model:

```bash
uv run --project services/tts pytest -q
```

After both vLLM and the adapter service are running, run a real end-to-end smoke test:

```bash
joyvl-tts-adapter smoke \
    --text "Hello, testing speech synthesis." \
    --output /tmp/joyvl_tts_smoke.pcm
```

When the test succeeds, `/tmp/joyvl_tts_smoke.pcm` exists, its file size is greater than 0, and the byte count is even because the output is raw `pcm16` audio.

The first real synthesis is usually much slower than later requests because the model needs to finish JIT compilation, CUDA graph capture, and internal cache initialization. `./services/tts/scripts/run.sh adapter` and `./services/tts/scripts/run.sh all` run an automatic warmup by default, so the user's first conversation turn usually does not pay that startup cost. If the adapter is started manually with `joyvl-tts-adapter --host ...`, run the smoke test above manually after the health check passes to use it as warmup.

You can also check both services:

```bash
curl http://127.0.0.1:8992/health
curl http://127.0.0.1:8991/v1/models
```

## Environment Variables

- `TTS_UPSTREAM_URL`: vLLM Omni upstream URL, default `ws://127.0.0.1:8991/v1/audio/speech/stream`
- `TTS_DEFAULT_VOICE`: real speaker used when WebUI passes `default`, default `vivian`
- `TTS_ADAPTER_HOST`: adapter listen host, default `0.0.0.0`
- `TTS_ADAPTER_PORT`: adapter listen port, default `8992`
- `TTS_GPU`: physical GPU ID exposed to the TTS model service, default `2`
- `TTS_TENSOR_PARALLEL_SIZE`: vLLM Omni tensor parallel size, default `1`
- `TTS_LOW_MEMORY_CONFIG`: use the repo low-memory deploy config by default, default `1`
- `TTS_DEPLOY_CONFIG`: custom vLLM Omni deploy config path, default `services/tts/config/qwen3_tts_lowmem.yaml`
- `TTS_GPU_MEMORY_UTILIZATION`: optional global GPU memory utilization override passed to every stage; unset by default so the deploy config controls per-stage memory (`0.3 + 0.3 = 0.6` total)
- `TTS_UPSTREAM_WAIT_INTERVAL`: polling interval used by `scripts/run-adapter.sh` while waiting for the upstream port, default `5` seconds
- `TTS_ENABLE_WARMUP`: whether `./services/tts/scripts/run.sh adapter` / `all` runs automatic warmup, default `1`; set to `0` to disable
- `TTS_WARMUP_TEXT`: short text for automatic warmup, default is a short greeting
- `TTS_WARMUP_OUTPUT`: automatic warmup output file, default `/tmp/joyvl_tts_warmup.pcm`
- `TTS_WARMUP_TIMEOUT`: timeout for one automatic warmup smoke test, default `180` seconds
