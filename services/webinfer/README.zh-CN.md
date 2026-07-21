# web_infer 概览

> 原文档: [README.md](README.md)

`web_infer` 是 StreamingHarness 的实时视频推理服务层。它暴露 OpenAI 兼容 HTTP API。它自身不加载模型，而是将请求转发给本地 vLLM OpenAI API 服务；同时，适配器维护视频帧、chunk、用户问题、中间摘要和长期记忆。

一句话概括：frontend/WebUI 将视频帧发送到 `8070`；`live_adapter.py` 组织上下文并调用主 VLM；后台摘要模型将历史帧压缩成文本记忆。

## 文件结构

| 文件 | 用途 |
| ---- | ------- |
| `live_adapter.py` | 核心 aiohttp 服务；实现 OpenAI 兼容 API、会话状态、chunk 记忆、主模型转发和输出持久化。 |
| `memory_summarizer.py` | 摘要组件；调用摘要 vLLM 生成 chunk 级中间摘要，并将多个摘要压缩为长期记忆。 |
| `scripts/run.sh` | 统一入口；集中管理 Python 和模型路径配置，然后调用下面的启动脚本。 |
| `scripts/start_adapter.sh` | 启动适配器，默认监听 `127.0.0.1:8070`。 |
| `scripts/start_all_models.sh` | 批量启动主模型 vLLM 服务。模型路径和名称默认由 `scripts/run.sh` 传入。 |
| `scripts/start_model.sh` | 启动一个主模型 vLLM OpenAI API 服务。 |
| `scripts/start_summary_model.sh` | 启动一个摘要 vLLM 服务；中间摘要和长期记忆压缩共用它。 |
| `deploy.md` | 前端 API 接入示例，包括请求/响应 JSON。 |
| `summary_vllm_logs/` | 来自 `scripts/start_summary_model.sh` 的日志和 PID 文件。 |

## 默认端口和模型

| 服务 | 端口 | 默认模型/路径 | 说明 |
| ------- | ---- | ------------------ | ----- |
| adapter | `8070` | `streaming-infer-adapter` | 外部 OpenAI 兼容 API。 |
| main model | `7060` | `/tmp/models/JoyAI-VL-Interaction-Preview` | 当请求使用 `model=JoyAI-VL-Interaction-Preview` 时路由到这里。 |
| summary model | `8065` | `/tmp/models/Qwen3-VL-4B-Instruct` | 默认仓库为 `Qwen/Qwen3-VL-4B-Instruct`；同时处理中间摘要和长期记忆压缩。 |

默认 GPU 分配：摘要模型使用 `0`，主模型使用 `3`。`scripts/run.sh` 会先尝试使用安装脚本创建的共享环境 `services/.venv`。如需指定其他环境，请传入 `VENV_ACTIVATE`，或设置 `PYTHON_BIN` 选择 Python 可执行文件。要强制使用当前 shell 环境，请设置 `VENV_ACTIVATE=`。

## 快速开始

```bash
# 从仓库根目录运行
cd services/webinfer

# 将模型下载到 /tmp/models
../../install/download-models.sh --all

# 在后台启动一个摘要服务；日志写入 summary_vllm_logs/
bash scripts/run.sh summary

# 在前台启动主模型
bash scripts/run.sh models

# 在前台启动适配器
bash scripts/run.sh adapter
```

也可以让 `scripts/run.sh` 串联三个启动阶段：

```bash
bash scripts/run.sh all
```

检查：

```bash
curl http://127.0.0.1:8070/health
curl http://127.0.0.1:8070/v1/models
```

前端或 WebUI 使用的 OpenAI base URL：

```text
http://127.0.0.1:8070/v1
```

## 外部 API

`web_infer` 只提供 HTTP API。它不提供 WebSocket 支持，也不处理音频。

| 端点 | 用途 |
| -------- | ------- |
| `GET /health` | 健康检查；返回后端列表、会话数量和摘要器状态。 |
| `GET /v1/models` | 返回可用主模型；默认是 `JoyAI-VL-Interaction-Preview`。 |
| `POST /v1/chat/completions` | 核心端点；兼容 OpenAI chat completions，并支持图像帧。 |
| `POST /v1/streaming/reset` | 重置会话并清空已有输出。 |

推荐将 session 放在 header 中：

```http
x-streaming-session: <sessionId>
```

也可以放在 body 的 `user` 字段中。没有 session 时，请求会共用 `default`，这很容易让不同客户端之间混合状态。

推荐的图像输入格式是 OpenAI 格式：

```json
{
  "type": "image_url",
  "image_url": { "url": "data:image/jpeg;base64,..." }
}
```

也支持 `file:///absolute/path.jpg`，但必须配置 `ALLOWED_LOCAL_IMAGE_ROOTS`，并且文件必须位于允许目录下。更完整的前端请求示例见 `deploy.md`。

## 推理和记忆流程

1. 适配器从请求中提取 session、model、messages、images 和 timestamps。
2. 如果没有图像，它会将文本请求直接转发给主模型。
3. 如果存在图像，每一帧会被封装为内部消息：`<time range>` 加上图像。
4. 默认情况下，prompt 被视为当前用户问题。该问题会一直保留，直到新的 prompt 替换它。
5. 默认 `FORCE_SILENCE_BEFORE_QUERY=true`：没有用户问题时，适配器会直接返回 `</silence>`，并且不调用主模型。
6. 调用主模型前，适配器会注入 `Video History`、`Q&A History` 和当前 `User Query`。
7. 主模型输出会归一化为两种格式之一：
   - `</silence>`
   - `</response> one-sentence reply`
8. 每满 `CHUNK` 帧，会生成一条中间摘要。每累计 `COMPRESS_EVERY_N_CHUNKS` 条中间摘要，它们会被压缩为长期记忆。
9. 响应以标准 chat completion JSON 返回，并带有额外的 `streamingharness.memory/timing/raw_content` 字段。

## 关键参数

| 参数/环境变量 | 默认值 | 说明 |
| ------------------------------ | ------- | ----------- |
| `VENV_ACTIVATE` | 空 | 可选虚拟环境 activate 脚本路径。 |
| `PYTHON_BIN` | `python` | 用于启动 vLLM 和适配器的 Python 可执行文件。 |
| `STREAMING_MODEL_REPO` | `jdopensource/JoyAI-VL-Interaction-Preview` | `../../install/download-models.sh --all` 使用的 Hugging Face 仓库。 |
| `MODEL_PATH` | `/tmp/models/JoyAI-VL-Interaction-Preview` | 主模型本地路径。 |
| `SUMMARY_MODEL_REPO` | `Qwen/Qwen3-VL-4B-Instruct` | `../../install/download-models.sh --all` 使用的摘要模型 Hugging Face 仓库。 |
| `SUMMARY_MODEL_PATH` | `/tmp/models/Qwen3-VL-4B-Instruct` | 摘要模型本地路径；如果不存在，请先下载。 |
| `ADAPTER_PORT` | `8070` | 适配器监听端口。 |
| `MAIN_BACKENDS` | 见 `scripts/start_adapter.sh` | 主模型后端 JSON，按请求中的 `model` 路由。 |
| `MAIN_MAX_TOKENS` | 脚本默认 `256` | 主模型输出长度。 |
| `MAIN_TEMPERATURE` | `0.8` | 主模型采样温度。 |
| `CHUNK` | 脚本默认 `100` | 每个记忆 chunk 的帧数。 |
| `COMPRESS_EVERY_N_CHUNKS` | `5` | 压缩为长期记忆前累计的中间摘要数量。 |
| `ASYNC_SUMMARY_LEAD_FRAMES` | 脚本默认 `20` | 在 chunk 边界前异步生成摘要，以减少等待。 |
| `FRAME_SECONDS` | `1.0` | 缺少显式时间戳时的估算帧时长。 |
| `SUMMARIZER_KEY_FRAMES` | `0` | `0` 表示用 chunk 中所有帧做摘要；大于 0 时均匀采样帧。 |
| `SUMMARIZER_MAX_PIXELS` | `262144` | 摘要输入图像的最大像素数。 |
| `SUMMARIZER_API_BASE` | `http://127.0.0.1:8065/v1` | 中间摘要和长期记忆压缩共用的摘要模型 OpenAI API。 |
| `SUMMARIZER_MODEL` | `/tmp/models/Qwen3-VL-4B-Instruct` | 发送给摘要 vLLM 的模型名称。 |
| `LIVE_SAVE_OUTPUTS` | 脚本默认 `true` | 是否保存实时输出 JSON。 |
| `FRAME_SAVE_DIR` | `/tmp/streaming_adapter_frames` | 会话帧目录；注意 data URL 当前并不会真正持久化为图像文件。 |
| `ALLOWED_LOCAL_IMAGE_ROOTS` | 默认与 `FRAME_SAVE_DIR` 相同 | 允许 `file://` 或本地路径图像引用的根目录。 |

输出默认写入：

```text
result_v2
```

其中包括完整输出、轻量输出和可选调试输入。默认保留 QA history；传入 `--no-qa-history` 可禁用。

## 停止服务

推荐使用停止脚本：

```bash
bash scripts/stop.sh
```

摘要服务是后台 `nohup` 进程：

```bash
kill $(cat summary_vllm_logs/vllm_8065.pid)
```

`scripts/start_all_models.sh` 和 `scripts/start_adapter.sh` 是前台进程，通常用 `Ctrl+C` 停止。停止后检查端口：

```bash
ss -ltnp | rg ':(7060|8065|8070)\b'
```

## 常见问题

- 适配器成功启动并不代表模型可用；请求时出现 `502` 通常表示后端 vLLM 端口尚未启动。
- 浏览器直接跨域访问可能失败；请使用 WebUI 或同源代理。
- `/v1/chat/completions` 没有实现 SSE streaming。它返回普通 JSON。
- 默认情况下，没有 query 会强制静默，这可能看起来像“模型不说话”；请传入 prompt，或禁用 `FORCE_SILENCE_BEFORE_QUERY`。
- `file://` 图像默认受 `ALLOWED_LOCAL_IMAGE_ROOTS` 限制，位于 allowlist 之外会被拒绝。
- 中间摘要和长期记忆压缩共用 `SUMMARIZER_API_BASE/SUMMARIZER_MODEL`。如果上下文超过限制，请降低 `LONG_TERM_MAX_TOKENS/LONG_TERM_TARGET_TOKEN_COUNT`，或提高 `SUMMARY_MAX_MODEL_LEN`。
- `SUMMARIZER_KEY_FRAMES=0` 会将 chunk 中每一帧都发送给摘要模型，chunk 较大时速度更慢，请求也更大。
- `scripts/start_summary_model.sh` 会覆盖 PID 文件。再次运行前，请检查端口和旧进程。
