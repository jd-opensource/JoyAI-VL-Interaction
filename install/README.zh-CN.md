# JoyVL 安装兼容性说明

> 原文档: [README.md](README.md)

这个 `install` 目录用于将核心 WebUI 安装、可选服务适配器和较重的模型运行时环境分开管理。`install/` 不再提供服务启动入口；启动脚本位于 `services/` 下，各组件的服务级脚本位于其 `scripts/` 目录中。

除非另有说明，请从仓库根目录运行下面的命令。

所有默认模型权重路径都位于 `/tmp/models/<model-name>`。当前默认值为：

- 主交互模型：`/tmp/models/JoyAI-VL-Interaction-Preview`，默认仓库 `jdopensource/JoyAI-VL-Interaction-Preview`
- 摘要模型：`/tmp/models/Qwen3-VL-4B-Instruct`，默认仓库 `Qwen/Qwen3-VL-4B-Instruct`
- ASR 模型：`/tmp/models/Qwen3-ASR-1.7B`，默认仓库 `Qwen/Qwen3-ASR-1.7B`
- TTS 模型：`/tmp/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`，默认仓库 `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`

如果默认目录不存在或为空，请先使用统一下载脚本下载权重。不要回退到项目内部的 `models/` 子目录：

```bash
./install/download-models.sh --all
```

## 核心安装

- `install.sh` 使用 `uv venv` 创建虚拟环境，然后用 `uv pip install` 下载并安装依赖。
- `install.sh` 以 editable 模式安装 WebUI。
- `install.sh` 固定 `vllm==0.22.0`。
- `install.sh` 默认使用 `constraints.txt` 约束与 vLLM 相关的传递 Web 栈依赖。
- 本安装目录统一使用 Python 3.12。
- `vllm==0.22.0` 支持 Python `>=3.10,<3.15`，但本项目使用 Python 3.12 安装并测试。它会拉取较重的 PyTorch/CUDA 依赖，因此推荐使用干净的新虚拟环境。
- WebUI 模板本身没有直接声明 FastAPI。启用可选适配器时会安装 FastAPI，`vllm==0.22.0` 也可能通过传递依赖安装 FastAPI。

### vLLM Web 栈约束

`vllm==0.22.0` 声明了较宽泛的依赖：

- `fastapi[standard]>=0.115.0`
- `prometheus-fastapi-instrumentator>=7.0.0`

如果没有约束，当前解析器可能选择 `fastapi==0.137.x`、`prometheus-fastapi-instrumentator==8.0.0` 或 `starlette==1.x`。在 `fastapi==0.137.x` 下，一些路由在 `include_router` 后仍会保留为 `_IncludedRouter`，而当前 vLLM 使用的指标中间件仍会从旧路由结构读取 `.path`。这种组合可能导致 vLLM OpenAI API 请求在指标中间件内部失败：

```text
AttributeError: '_IncludedRouter' object has no attribute 'path'
```

因此，`constraints.txt` 固定：

```text
fastapi<0.137
prometheus-fastapi-instrumentator<8
```

这些约束会让解析器选择仍与 `vllm==0.22.0` 兼容的 FastAPI/Starlette 0.x 栈。测试中，`fastapi==0.136.0` 仍会将 router 展开为常规 `APIRoute` 对象，而 `fastapi==0.137.0` 开始产生 `_IncludedRouter`。

## 可选适配器服务

这些选项只安装轻量级适配器/API 包：

- `--with-asr`：安装 FastAPI ASR WebSocket 适配器服务。
- `--with-tts`：安装 FastAPI TTS WebSocket 适配器服务。
- `--with-background-agent`：安装 FastAPI Codex 后台 agent API。
- `--with-all`：安装上述所有可选包。

这些包依赖常见 Web 服务库，例如 FastAPI、Uvicorn、WebSockets、HTTPX 和 Pydantic。它们不会安装 ASR nightly vLLM、vLLM Omni、模型权重或 CUDA 特定 wheel。

## ASR 运行时环境

`services/asr/README.md` 使用 Python 3.12、vLLM nightly 和 CUDA 12.9 index。除非你明确希望替换主环境中固定的 `vllm==0.22.0`，否则不要把该运行时混入核心 WebUI 环境。

安装 ASR 适配器：

```bash
./install/install.sh --with-asr
```

如果你按照 ASR README 启动真实 ASR 模型服务，请使用独立环境。

安装真实 ASR 模型服务运行时：

```bash
./install/install-audio-runtime.sh --asr
./install/download-models.sh --all
```

默认下载路径为 `/tmp/models/Qwen3-ASR-1.7B`。

启动它：

```bash
./services/asr/scripts/run.sh all
```

## TTS 运行时环境

TTS 适配器可以共享核心环境，但真实 TTS 模型服务需要 `vllm-omni==0.22.0` 与 `vllm==0.22.0` 一起使用。本安装目录统一使用 Python 3.12。安装真实 TTS 环境时，请在同一个安装命令中解析 `vllm==0.22.0` 和 `vllm-omni==0.22.0`，并继续使用 `constraints.txt` 约束 vLLM Web 栈。

安装 TTS 适配器：

```bash
./install/install.sh --with-tts
```

生产使用时，请在独立环境中安装并运行 vLLM Omni。

安装真实 TTS 模型服务运行时：

```bash
./install/install-audio-runtime.sh --tts
./install/download-models.sh --all
```

默认下载路径为 `/tmp/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`。

启动它：

```bash
./services/tts/scripts/run.sh all
```

`services/tts/scripts/run.sh all` 会先启动 TTS vLLM Omni，等待上游端口可用（默认每 5 秒重试一次），然后在适配器 `/health` 端点就绪后，在后台运行一次真实端到端预热：

```bash
joyvl-tts-adapter smoke --text "Hello." --output /tmp/joyvl_tts_warmup.pcm --timeout 180
```

这会在用户流量到来前消耗掉 Triton JIT、CUDA graph capture、code predictor 预热和缓存初始化的首次请求成本。测试中，冷启动的第一次 TTS 响应可能需要几十秒；预热后，后续请求会回到正常延迟。设置 `TTS_ENABLE_WARMUP=0` 可禁用预热。使用 `TTS_WARMUP_TEXT`、`TTS_WARMUP_OUTPUT` 和 `TTS_WARMUP_TIMEOUT` 可修改预热文本、输出文件和超时时间。

## 后台 Agent

安装：

```bash
./install/install.sh --with-background-agent --max-subagents 6
```

安装脚本会写入：

- `services/background-agent/background-agent.env`

启动：

```bash
./services/background-agent/scripts/run.sh
```

`--max-subagents N` 会同时配置 `CODEX_API_MAX_SUBAGENTS` 和 `BACKGROUND_MAX_SUBAGENTS`。当前项目默认值为 `6`。
