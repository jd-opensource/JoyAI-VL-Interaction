# JoyVL TTS 适配器服务

> 原文档: [README.md](README.md)

该目录提供 `services/webui` 使用的最小 TTS 适配器服务。它将当前 WebUI TTS WebSocket 协议转换为 vLLM Omni Qwen3-TTS Speech WebSocket 协议。

WebUI 侧使用原始 `pcm16` 音频流。适配器向 vLLM Omni 请求 `response_format: "pcm"`，并将上游 PCM 字节直接转发给 WebUI 播放。

## 安装

从仓库根目录运行以下命令。

```bash
uv venv -p python3.12 --seed services/tts/.venv
source services/tts/.venv/bin/activate
uv pip install -c install/constraints.txt \
    vllm==0.22.0 vllm-omni==0.22.0 \
    --torch-backend=auto
uv pip install -c install/constraints.txt -e "services/tts[dev]"
```

说明：该运行时固定为 Python 3.12、`vllm-omni==0.22.0` 和 `vllm==0.22.0`。vLLM Omni 和 vLLM 的主版本必须匹配。如果将 `vllm==0.23.0` 与当前 `vllm-omni==0.22.0` 一起安装，启动会因 entrypoint 兼容性问题失败。

首次使用前，将模型下载到统一的 `/tmp/models` 目录：

```bash
./install/download-models.sh --all
```

## 启动 vLLM Omni

推荐使用组件启动脚本：

```bash
./services/tts/scripts/run.sh model
```

先定位 vLLM Omni 自带的 Qwen3-TTS 部署配置：

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

然后在端口 `8991` 启动 vLLM Omni。组件脚本默认使用物理 GPU 2，并加载 `services/tts/config/qwen3_tts_lowmem.yaml`，这是面向短交互回复的单卡部署配置。它将 text/talker 阶段限制为 1024 token，将 code2wav 阶段保持为 8192 codec token，并对两个 TTS 阶段分别使用 `gpu_memory_utilization=0.3`，总 TTS 预算为 `0.6`。

等价的手动命令为：

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

设置 `TTS_LOW_MEMORY_CONFIG=0` 可使用 vLLM Omni 自带的 `qwen3_tts.yaml`，或设置 `TTS_DEPLOY_CONFIG=/path/to/config.yaml` 来提供自定义部署文件。

## 启动适配器服务

推荐使用组件启动脚本。它会等待 TTS vLLM Omni 端口可用（默认每 5 秒重试一次），然后启动适配器。`/health` 就绪后，它会通过完整的 `adapter -> vLLM Omni -> audio` 路径在后台自动运行一次短文本预热，在用户流量到来前消耗 Triton JIT、CUDA graph capture 和 code predictor 预热等冷启动成本：

```bash
./services/tts/scripts/run.sh adapter
```

默认预热会合成一段短问候，并将输出写到 `/tmp/joyvl_tts_warmup.pcm`。如果只想手动启动适配器，运行：

```bash
joyvl-tts-adapter --host 0.0.0.0 --port 8992
```

也可以通过统一入口同时启动模型和适配器：

```bash
./services/tts/scripts/run.sh all
```

健康检查：

```bash
curl http://127.0.0.1:8992/health
```

WebUI 默认使用的 TTS 地址为：

```text
ws://127.0.0.1:8992/ws/tts
```

## 测试

不需要真实模型的单元测试：

```bash
uv run --project services/tts pytest -q
```

vLLM 和适配器服务都运行后，执行一次真实端到端冒烟测试：

```bash
joyvl-tts-adapter smoke \
    --text "Hello, testing speech synthesis." \
    --output /tmp/joyvl_tts_smoke.pcm
```

测试成功时，`/tmp/joyvl_tts_smoke.pcm` 会存在，文件大小大于 0，并且字节数为偶数，因为输出是原始 `pcm16` 音频。

第一次真实合成通常会比后续请求慢很多，因为模型需要完成 JIT 编译、CUDA graph capture 和内部缓存初始化。`./services/tts/scripts/run.sh adapter` 和 `./services/tts/scripts/run.sh all` 默认会自动预热，因此用户第一次对话通常不会承担这部分启动成本。如果用 `joyvl-tts-adapter --host ...` 手动启动适配器，请在健康检查通过后手动运行上面的冒烟测试，将其作为预热。

也可以同时检查两个服务：

```bash
curl http://127.0.0.1:8992/health
curl http://127.0.0.1:8991/v1/models
```

## 环境变量

- `TTS_UPSTREAM_URL`：vLLM Omni 上游 URL，默认 `ws://127.0.0.1:8991/v1/audio/speech/stream`
- `TTS_DEFAULT_VOICE`：当 WebUI 传入 `default` 时使用的真实说话人，默认 `vivian`
- `TTS_ADAPTER_HOST`：适配器监听 host，默认 `0.0.0.0`
- `TTS_ADAPTER_PORT`：适配器监听端口，默认 `8992`
- `TTS_GPU`：暴露给 TTS 模型服务的物理 GPU ID，默认 `2`
- `TTS_TENSOR_PARALLEL_SIZE`：vLLM Omni tensor parallel 大小，默认 `1`
- `TTS_LOW_MEMORY_CONFIG`：默认使用仓库中的低显存部署配置，默认 `1`
- `TTS_DEPLOY_CONFIG`：自定义 vLLM Omni 部署配置路径，默认 `services/tts/config/qwen3_tts_lowmem.yaml`
- `TTS_GPU_MEMORY_UTILIZATION`：传给每个阶段的可选全局 GPU 显存使用率覆盖；默认不设置，由部署配置控制各阶段显存（`0.3 + 0.3 = 0.6` 总计）
- `TTS_UPSTREAM_WAIT_INTERVAL`：`scripts/run-adapter.sh` 等待上游端口时的轮询间隔，默认 `5` 秒
- `TTS_ENABLE_WARMUP`：`./services/tts/scripts/run.sh adapter` / `all` 是否运行自动预热，默认 `1`；设为 `0` 可禁用
- `TTS_WARMUP_TEXT`：自动预热短文本，默认为一段短问候
- `TTS_WARMUP_OUTPUT`：自动预热输出文件，默认 `/tmp/joyvl_tts_warmup.pcm`
- `TTS_WARMUP_TIMEOUT`：一次自动预热冒烟测试的超时时间，默认 `180` 秒
