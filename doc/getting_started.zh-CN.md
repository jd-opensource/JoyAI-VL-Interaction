# 入门指南

> 原文档: [getting_started.md](getting_started.md)

## 前置条件

- 带 NVIDIA GPU 的 Linux（已在 NVIDIA Hopper 系列显卡上完成测试）
- CUDA 12.x + NVIDIA driver 535+
- Python 3.12（推荐）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip，用于 Python 包管理

## 安装

使用提供的安装脚本设置所有依赖：

```bash
# 安装核心依赖（webinfer + webui）
./install/install.sh --with-all

# 安装 ASR/TTS 运行时（可选）
./install/install-audio-runtime.sh --all

# 下载所有模型权重
./install/download-models.sh --all
```

跨平台兼容性说明请参阅 `install/README.md`。

`install/` 只用于依赖设置、模型下载和生成配置。运行时入口位于 `services/`：使用 `services/scripts/run.sh` 进行编排，或使用 `services/<service>/scripts/` 下的脚本进行组件级操作。

## 模型下载

启动前先下载模型权重：

```bash
# 所有模型：主交互模型 + 摘要模型 + ASR + TTS
./install/download-models.sh --all
```

默认模型路径：

| 模型 | 默认路径 | HuggingFace 仓库 |
|-------|-------------|------------------|
| 主交互模型 | `/tmp/models/JoyAI-VL-Interaction-Preview` | `jdopensource/JoyAI-VL-Interaction-Preview` |
| 摘要模型 | `/tmp/models/Qwen3-VL-4B-Instruct` | `Qwen/Qwen3-VL-4B-Instruct` |
| ASR 模型（可选） | `/tmp/models/Qwen3-ASR-1.7B` | `Qwen/Qwen3-ASR-1.7B` |
| TTS 模型（可选） | `/tmp/models/Qwen3-TTS-12Hz-1.7B-CustomVoice` | `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` |

## 最小部署（2 个服务）

只启动核心服务，即视频推理和 Web UI：

```bash
# 后台启动 webinfer，并以前台方式启动 WebUI
./services/scripts/run.sh minimal
```

`services/scripts/run.sh minimal` 会在后台启动 `webinfer`，并让 WebUI 保持在前台运行。使用 `services/scripts/run.sh` 时，只有最终的 WebUI 进程启动后，启动流程才算完成。在该终端按 `Ctrl+C` 可停止由编排器启动的服务。

在浏览器中打开：`https://127.0.0.1:8099`

## 完整部署（所有服务）

可选服务必须在 **Web UI 之前**启动。

### 推荐启动顺序

```text
1. services/webinfer          （必需）
2. ASR                        （可选，启用语音输入）
3. TTS                        （可选，启用语音输出）
4. background-agent           （可选，启用任务委托）
5. services/webui             （必需，最后启动）
```

### 分步说明

如果保留可选的 `background-agent`，请先准备 `CODEX_HOME`，并建议提前阅读
[background-agent README](../services/background-agent/README.zh-CN.md)。你可以把
`CODEX_HOME` 指向已有 Codex home，也可以把 `auth.json` 和 `config.toml` 复制到
默认服务路径：

```bash
# 方式 1：让 background-agent 使用已有的 Codex home
export CODEX_HOME=/path/to/your/codex-home

# 方式 2：使用默认的服务内 Codex home
mkdir -p services/background-agent/codex-home
cp /path/to/your/codex-home/{auth.json,config.toml} services/background-agent/codex-home/
```

一条命令启动完整服务集：

```bash
./services/scripts/run.sh all
```

`services/scripts/run.sh all` 会在 WebUI 之前启动可选服务。可使用 `START_ASR=0`、`START_TTS=0` 或 `START_BACKGROUND_AGENT=0` 跳过对应可选服务。启用 `background-agent` 时，它的 `CODEX_HOME` 必须包含 `config.toml` 和 `auth.json`。使用该编排器时，只有最终的 WebUI 进程启动后，完整服务集才算就绪。在该终端按 `Ctrl+C` 可停止由编排器启动的服务。

也可以按下面顺序手动启动服务：

```bash
# 1. 推理后端
(cd services/webinfer && bash scripts/run.sh all)

# 2. ASR（可选）
./services/asr/scripts/run.sh all

# 3. TTS（可选）
./services/tts/scripts/run.sh all

# 4. 后台 Agent（可选）
./services/background-agent/scripts/run.sh

# 5. Web UI（最后启动）
source services/.venv/bin/activate
(cd services/webui && bash scripts/start_server.sh)
```

## 健康检查

启动后，确认各服务正在运行：

```bash
curl http://127.0.0.1:8070/health   # webinfer
curl http://127.0.0.1:8994/health   # ASR（可选）
curl http://127.0.0.1:8992/health   # TTS（可选）
curl http://127.0.0.1:8079/health   # background-agent（可选）
```

Web UI 可通过 `https://127.0.0.1:8099` 访问（接受自签名证书警告）。

## RTSP 本地推流测试

WebUI 可以使用摄像头，也可以使用 RTSP 输入。如果没有真实 IP 摄像头，可以在本机运行 MediaMTX 服务，并用 `ffmpeg` 把本地视频文件推成 RTSP 流。

本地流启动后，在 WebUI 的 RTSP 输入框填写类似 `rtsp://127.0.0.1:8554/fire1` 的地址。如果 WebUI 运行在另一台机器上，请把 `127.0.0.1` 换成运行 MediaMTX 的机器地址。

MediaMTX 下载说明、辅助脚本示例和常见检查请参阅 [RTSP 本地推流说明](rtsp_streaming.zh-CN.md)。

## 停止服务

如果通过 `services/scripts/run.sh minimal` 或 `services/scripts/run.sh all` 启动，请在该终端按 `Ctrl+C`。如需从另一个终端停止服务：

```bash
./services/scripts/stop.sh all
```

## 配置

### webinfer

关键环境变量（在启动脚本中设置，或启动前导出）：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `PYTHON_BIN` | `python` | vLLM 和适配器使用的 Python 可执行文件 |
| `VENV_ACTIVATE` | 自动检测 `services/.venv` | 可选虚拟环境 activate 脚本路径；设置 `VENV_ACTIVATE=` 可使用当前 shell 环境 |
| `MODEL_PATH` | `/tmp/models/JoyAI-VL-Interaction-Preview` | 主模型本地路径 |
| `SUMMARY_MODEL_PATH` | `/tmp/models/Qwen3-VL-4B-Instruct` | 摘要模型本地路径 |
| `MAIN_GPU` | `0` | 流式模型服务使用的单张物理 GPU |
| `SUMMARY_GPU` | `1` | 摘要模型服务使用的单张物理 GPU |
| `ADAPTER_PORT` | `8070` | 适配器监听端口 |
| `CHUNK` | `100` | 每个记忆 chunk 的帧数 |
| `COMPRESS_EVERY_N_CHUNKS` | `5` | 多少个 chunk 后进行长期记忆压缩 |
| `MAIN_MAX_TOKENS` | `256` | 主模型最大输出 token 数 |
| `MAIN_TEMPERATURE` | `0.8` | 采样温度 |
| `FORCE_SILENCE_BEFORE_QUERY` | `true` | 没有用户问题时抑制输出 |

### ASR

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `ASR_UPSTREAM_URL` | `http://127.0.0.1:8993/v1/audio/transcriptions` | vLLM 端点 |
| `ASR_MODEL` | `Qwen/Qwen3-ASR-1.7B` | 模型名称 |
| `ASR_ADAPTER_PORT` | `8994` | 适配器监听端口 |
| `ASR_GPU` | `2` | ASR 模型服务使用的单张物理 GPU |
| `ASR_GPU_MEMORY_UTILIZATION` | `0.3` | vLLM GPU 显存使用上限 |

### TTS

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `TTS_UPSTREAM_URL` | `ws://127.0.0.1:8991/v1/audio/speech/stream` | vLLM-Omni 端点 |
| `TTS_DEFAULT_VOICE` | `vivian` | 默认说话人音色 |
| `TTS_ADAPTER_PORT` | `8992` | 适配器监听端口 |
| `TTS_GPU` | `2` | TTS 模型服务使用的单张物理 GPU |
| `TTS_DEPLOY_CONFIG` | `services/tts/config/qwen3_tts_lowmem.yaml` | 短回复 vLLM-Omni 部署配置，总 TTS 显存预算为 `0.6` |

### background-agent

background-agent 会封装本地 `codex` CLI，用于委托后台任务。它的默认运行配置位于
`services/background-agent`：

| 变量 | 默认值 | 说明 |
|----------|---------|-------------|
| `CODEX_HOME` | `services/background-agent/codex-home` | Codex home，需包含 `config.toml` 和 `auth.json`；可以指向已有 Codex home，或把这两个文件复制到默认路径 |
| `CODEX_API_WORKSPACE` | `<repo>/agent-workspace` | background Codex 运行使用的工作区；启动时自动创建 |
| `CODEX_API_HOST` | `127.0.0.1` | background-agent 绑定地址 |
| `CODEX_API_PORT` | `8079` | background-agent 监听端口 |
| `CODEX_API_MAX_SUBAGENTS` | `6` | API wrapper 使用的最大并行 subagent 数量 |
| `BACKGROUND_AGENT_API_URL` | `http://127.0.0.1:8079` | WebUI 访问 background-agent 使用的 URL |

默认 `CODEX_HOME` 使用服务内路径，方便 background-agent 独立于当前 shell 运行。
首次配置时，请在启动前执行 `export CODEX_HOME=/path/to/your/codex-home`，
或把 `auth.json` 和 `config.toml` 复制到 `services/background-agent/codex-home/`。

## 故障排查

常见启动和运行问题请参阅 [故障排查指南](troubleshooting.zh-CN.md)。其中包含模型就绪、
ASR/TTS 连接、GPU 显存、本地图片路径，以及 `background-agent` 的 `CODEX_HOME` 配置问题。
