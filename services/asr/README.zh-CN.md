# JoyVL ASR 适配器服务

> 原文档: [README.md](README.md)

该目录提供 `services/webui` 使用的 ASR 适配器服务。它从 WebUI ASR 桥接层接收 `pcm16` 音频包，将音频封装为 WAV，发送到 vLLM `/v1/audio/transcriptions` 端点，并以现有 WebUI `asr.py` 能解析的格式返回识别结果。

## 安装

从仓库根目录运行以下命令。

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

## 下载模型

```bash
./install/download-models.sh --all
```

## 启动 vLLM ASR

推荐使用组件启动脚本：

```bash
./services/asr/scripts/run.sh model
```

下面的命令使用物理 GPU 2 作为单卡服务，并将 vLLM GPU 显存使用率限制为 `0.3`。如需使用其他 GPU，请修改 `CUDA_VISIBLE_DEVICES`：

```bash
CUDA_VISIBLE_DEVICES=2 vllm serve /tmp/models/Qwen3-ASR-1.7B \
    --served-model-name Qwen/Qwen3-ASR-1.7B \
    --host 0.0.0.0 \
    --port 8993 \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.3 \
    --trust-remote-code
```

检查 vLLM：

```bash
curl http://127.0.0.1:8993/v1/models
```

## 启动适配器服务

推荐使用组件启动脚本。它会等待 ASR vLLM 端口可用（默认每 5 秒重试一次），然后启动适配器：

```bash
./services/asr/scripts/run.sh adapter
```

如果只想手动启动适配器，运行：

```bash
joyvl-asr-adapter --host 0.0.0.0 --port 8994
```

也可以通过统一入口同时启动模型和适配器：

```bash
./services/asr/scripts/run.sh all
```

健康检查：

```bash
curl http://127.0.0.1:8994/health
```

WebUI 默认使用的 ASR 上游地址为：

```text
ws://127.0.0.1:8994/ws/asr
```

## 测试

不需要真实模型的单元测试：

```bash
uv run --project services/asr pytest -q
```

vLLM 和适配器服务都运行后，执行一次真实端到端冒烟测试：

```bash
joyvl-asr-adapter smoke --wav <mono-pcm16-wav-file>
```

## 环境变量

- `ASR_UPSTREAM_URL`：vLLM 转写 API URL，默认 `http://127.0.0.1:8993/v1/audio/transcriptions`
- `ASR_MODEL`：vLLM 模型名称，默认 `Qwen/Qwen3-ASR-1.7B`
- `ASR_SAMPLE_RATE`：默认采样率，默认 `16000`
- `ASR_ADAPTER_HOST`：适配器监听 host，默认 `0.0.0.0`
- `ASR_ADAPTER_PORT`：适配器监听端口，默认 `8994`
- `ASR_GPU`：暴露给 ASR 模型服务的物理 GPU ID，默认 `2`
- `ASR_TENSOR_PARALLEL_SIZE`：vLLM tensor parallel 大小，默认 `1`
- `ASR_GPU_MEMORY_UTILIZATION`：vLLM GPU 显存使用率上限，默认 `0.3`
- `ASR_UPSTREAM_WAIT_INTERVAL`：`scripts/run-adapter.sh` 等待上游端口时的轮询间隔，默认 `5` 秒

## 参考

- vLLM Qwen3-ASR recipe: https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3-ASR.html
- Qwen3-ASR model card: https://huggingface.co/Qwen/Qwen3-ASR-1.7B
