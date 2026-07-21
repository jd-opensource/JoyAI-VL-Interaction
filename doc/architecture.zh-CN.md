# 系统架构

> 原文档: [architecture.md](architecture.md)

## 概览

JoyAI-VL-Interaction 是一个围绕视觉语言交互模型构建的实时视频语言交互系统。系统会持续观察实时视频流，自主决定何时说话、何时保持静默，或何时委托给后台 agent，并在需要时于一秒内响应。

系统由五个服务组成，并以 WebUI 为中心形成 hub-and-spoke 拓扑：

```text
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

## 组件职责

| 服务 | 目录 | 必需 | 角色 |
|---------|-----------|----------|------|
| **webinfer** | `services/webinfer` | 是 | 实时视频推理。暴露 OpenAI 兼容 HTTP API。管理视频帧、chunk 记忆、中期摘要和长期记忆，并将请求转发到本地 vLLM 后端。 |
| **webui** | `services/webui` | 是 | 浏览器前端和 WebRTC 服务器。处理摄像头/视频流输入，渲染交互 UI，并桥接 ASR/TTS/background-agent 连接。 |
| **asr** | `services/asr` | 否 | 语音识别适配器。从 WebUI 接收 PCM16 音频，经 vLLM（Qwen3-ASR）转写，并通过 WebSocket 返回结果。 |
| **tts** | `services/tts` | 否 | 语音合成适配器。通过 vLLM-Omni（Qwen3-TTS）将模型文本回复转换为 PCM16 音频，并通过 WebSocket 流式返回。 |
| **background-agent** | `services/background-agent` | 否 | 后台任务 agent。处理交互模型委托的复杂或耗时问题，使用具备代码执行能力的 LLM agent。 |

## 数据流

1. **视频输入**：WebUI 通过 WebRTC 捕获摄像头帧（或 RTSP 流），并以约 1 fps 向 `webinfer` 发送 JPEG 帧。
2. **推理**：`webinfer` 的适配器组装上下文（当前帧、视频历史、问答历史、长期记忆），并通过 vLLM 调用主 VLM。
3. **决策输出**：模型返回三种信号之一：
   - `</silence>`：无须发言
   - `</response> text`：主动或响应式发言
   - `</delegate> task`：交给后台 agent
4. **记忆**：每 N 帧形成一个 “chunk”；摘要模型将每个 chunk 压缩为中期摘要。多个摘要会进一步压缩为长期记忆。
5. **语音输入输出**（可选）：如果 ASR 正在运行，用户语音会被转写并注入为用户问题。如果 TTS 正在运行，模型文本回复会被合成并播放。
6. **委托**（可选）：当模型发出委托时，WebUI 会将任务转发给 `background-agent`，后者异步运行并返回结果。

## 端口映射

| 端口 | 服务 | 协议 |
|------|---------|----------|
| 8070 | webinfer（适配器） | HTTP |
| 7060 | webinfer（主 VLM vLLM） | HTTP（内部） |
| 8065 | webinfer（摘要 VLM vLLM） | HTTP（内部） |
| 8099 | webui | HTTPS + WebSocket |
| 8994 | asr（适配器） | HTTP + WebSocket |
| 8993 | asr（vLLM ASR） | HTTP（内部） |
| 8992 | tts（适配器） | HTTP + WebSocket |
| 8991 | tts（vLLM-Omni） | HTTP + WebSocket（内部） |
| 8079 | background-agent | HTTP |

## GPU 分配（默认）

| GPU | 服务 | GPU 显存使用率 |
|-----|---------|------------------------|
| 0 | 主流式模型（vLLM，端口 7060） | `0.9` |
| 1 | 摘要模型（vLLM，端口 8065） | `0.9` |
| 2 | ASR 模型（vLLM，端口 8993） | `0.3` |
| 2 | TTS 模型（vLLM-Omni，端口 8991） | `0.6` 总部署预算 |

这些默认值在各自的启动脚本中设置。ASR 和 TTS 默认都通过 `ASR_GPU=2` 和 `TTS_GPU=2` 在 GPU 2 上以单卡方式运行。

## 运行时入口

安装和模型下载脚本位于 `install/`。运行时命令位于 `services/`：

使用 `services/scripts/run.sh` 时，只有最终的 WebUI 进程启动后，才应认为启动完成。

| 范围 | 入口 | 用途 |
|-------|------------|---------|
| 所有服务 | `services/scripts/run.sh` | 按推荐顺序启动最小或完整服务集。 |
| 停止服务 | `services/scripts/stop.sh` | 停止全部服务或某个服务组。 |
| webinfer | `services/webinfer/scripts/run.sh` | 启动 Web 推理模型和适配器。 |
| ASR | `services/asr/scripts/run.sh` | 启动 ASR 模型、适配器或两者。 |
| TTS | `services/tts/scripts/run.sh` | 启动 TTS 模型、适配器或两者。 |
| background-agent | `services/background-agent/scripts/run.sh` | 启动 Codex 后台 agent API。 |
| WebUI | `services/webui/scripts/start_server.sh` | 启动 HTTPS WebUI 服务器。 |
