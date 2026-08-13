# JoyVL Interaction WebUI

> 原文档: [README.md](README.md)

实时视觉语言模型交互 WebUI。默认情况下，它连接到本地 OpenAI 兼容 VLM 服务，用于本地摄像头或视频流交互预览。

## 环境设置

仓库级安装入口位于 `install/`，仓库级运行时入口是 `services/scripts/run.sh`。本 README 只说明单组件 WebUI 开发安装和启动。

需要 Python 3.12。

```bash
# 从仓库根目录运行
cd services/webui
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

默认后端地址为：

```text
http://127.0.0.1:8070/v1
```

请确保对应的 VLM 后端服务已经先启动。

## 启动

```bash
source ../.venv/bin/activate
./scripts/start_server.sh
```

在浏览器中打开：

```text
https://localhost:7099
```

如果浏览器提示自签名证书警告，请继续访问该站点。如果证书文件缺失，请先生成：

```bash
./scripts/generate_cert.sh
```

## LiveKit Server 与网络端口

WebUI 默认使用 **LiveKit Server 1.13.2**。首次启动时，如果 `services/webui/.livekit/livekit-server` 不存在，启动脚本会从 LiveKit 的 GitHub Releases 自动下载与当前 Linux 架构匹配的二进制文件，并在校验 SHA-256 后启动。支持 `x86_64/amd64` 和 `aarch64/arm64`。

默认版本可以通过 `LIVEKIT_VERSION` 环境变量覆盖：

```bash
LIVEKIT_VERSION=1.13.2 ./scripts/start_server.sh
```

`.livekit/` 是自动生成的本地运行目录，包含下载的二进制文件、配置和日志，不应提交到 Git。

远程访问需要开放 WebUI 端口和 LiveKit 媒体端口：

| 用途 | 协议与默认端口 | 是否需要对外开放 |
| --- | --- | --- |
| WebUI HTTPS 与代理后的 LiveKit 信令 | TCP `7099` | 是 |
| WebRTC 媒体传输 | UDP `8299` | 是 |
| WebRTC TCP fallback | TCP `8299` | 建议开放 |
| LiveKit 内部信令 | TCP `8298` | 否，仅监听 `127.0.0.1` |

WebUI 通过 `/livekit` 路径代理 LiveKit 信令，因此无需单独开放 `TCP 8298`。如使用 `--port` 修改 WebUI 端口，或使用 `LIVEKIT_UDP_PORT` / `LIVEKIT_TCP_PORT` 修改媒体端口，请在防火墙或云安全组中开放对应端口。默认情况下 `LIVEKIT_TCP_PORT` 与 `LIVEKIT_UDP_PORT` 相同，都是 `8299`。

以上是入站端口要求。首次自动下载 LiveKit Server 时，服务器还需要具备访问 GitHub Releases 的出站 HTTPS 能力，但无需为此增加入站端口。

## 上传视频输入

WebUI 可以先把视频上传到 `/tmp/joyvl-video-uploads`，但不会立即开始分析。上传完成后，点击 Start 才会在浏览器本地播放该文件。浏览器从实际显示画面中采样帧，通过 WebUI 已有的 `7099` 连接发送到服务端，再发布进本机 LiveKit 房间并由 VLM worker 订阅。这样远程浏览器无需访问 LiveKit RTC 媒体端口，同时仍保留 LiveKit 处理链路。上传视频不再启动 ffmpeg 或创建 RTSP 流，因此分析端不会跑到显示画面之前。session cleanup 会停止中继、LiveKit worker 和尚未完成的 VLM 请求。外部 RTSP 输入仍继续使用独立的 RTSP 处理链路。

该模式需要提前有 RTSP server（例如 MediaMTX）监听在配置的 RTSP 基址上。

## 常用端口

```bash
# 默认脚本：WebUI 7099，后端 8070
source ../.venv/bin/activate
./scripts/start_server.sh

# WebUI 8090，后端 8070
./scripts/start_server.sh --port 8090 --api-base http://127.0.0.1:8070/v1

# WebUI 8091，后端 8071
./scripts/start_server.sh --port 8091 --api-base http://127.0.0.1:8071/v1
```

## 停止

```bash
./scripts/stop_server.sh
```
