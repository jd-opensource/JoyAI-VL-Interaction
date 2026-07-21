# JoyVL ASR Adapter Service

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

This directory provides the ASR adapter service used by `services/webui`. It receives `pcm16` audio packets from the WebUI ASR bridge, wraps the audio as WAV, sends it to the vLLM `/v1/audio/transcriptions` endpoint, and returns recognition results in the format that the existing WebUI `asr.py` can parse.

## Installation

Run the following commands from the repository root.

```bash
uv venv -p python3.12 --seed services/asr/.venv
source services/asr/.venv/bin/activate
uv pip install -U vllm --pre \
    --extra-index-url https://wheels.vllm.ai/nightly/cu129 \
    --extra-index-url https://download.pytorch.org/whl/cu129 \
    --index-strategy unsafe-best-match
uv pip install "vllm[audio]"
uv pip install -e "services/asr[dev]"
```

## Download Models

```bash
./install/download-models.sh --all
```

## Start vLLM ASR

The recommended path is to use the component startup script:

```bash
./services/asr/scripts/run.sh model
```

The command below uses physical GPU 2 as a single-card service and caps vLLM GPU memory utilization at `0.3`. To use a different GPU, change `CUDA_VISIBLE_DEVICES`:

```bash
CUDA_VISIBLE_DEVICES=2 vllm serve /tmp/models/Qwen3-ASR-1.7B \
    --served-model-name Qwen/Qwen3-ASR-1.7B \
    --host 0.0.0.0 \
    --port 8993 \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.3 \
    --trust-remote-code
```

Check vLLM:

```bash
curl http://127.0.0.1:8993/v1/models
```

## Start the Adapter Service

The recommended path is to use the component startup script. It waits for the ASR vLLM port to become available, retrying every 5 seconds by default, and then starts the adapter:

```bash
./services/asr/scripts/run.sh adapter
```

To start only the adapter manually, run:

```bash
joyvl-asr-adapter --host 0.0.0.0 --port 8994
```

You can also start the model and adapter together through the unified entrypoint:

```bash
./services/asr/scripts/run.sh all
```

Health check:

```bash
curl http://127.0.0.1:8994/health
```

The ASR upstream address used by WebUI by default is:

```text
ws://127.0.0.1:8994/ws/asr
```

## Tests

Unit tests that do not require a real model:

```bash
uv run --project services/asr pytest -q
```

After both vLLM and the adapter service are running, run a real end-to-end smoke test:

```bash
joyvl-asr-adapter smoke --wav <mono-pcm16-wav-file>
```

## Environment Variables

- `ASR_UPSTREAM_URL`: vLLM transcription API URL, default `http://127.0.0.1:8993/v1/audio/transcriptions`
- `ASR_MODEL`: vLLM model name, default `Qwen/Qwen3-ASR-1.7B`
- `ASR_SAMPLE_RATE`: default sample rate, default `16000`
- `ASR_ADAPTER_HOST`: adapter listen host, default `0.0.0.0`
- `ASR_ADAPTER_PORT`: adapter listen port, default `8994`
- `ASR_GPU`: physical GPU ID exposed to the ASR model service, default `2`
- `ASR_TENSOR_PARALLEL_SIZE`: vLLM tensor parallel size, default `1`
- `ASR_GPU_MEMORY_UTILIZATION`: vLLM GPU memory utilization cap, default `0.3`
- `ASR_UPSTREAM_WAIT_INTERVAL`: polling interval used by `scripts/run-adapter.sh` while waiting for the upstream port, default `5` seconds

## References

- vLLM Qwen3-ASR recipe: https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-ASR.html
- Qwen3-ASR model card: https://huggingface.co/Qwen/Qwen3-ASR-1.7B
