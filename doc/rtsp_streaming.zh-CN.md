# RTSP 本地推流说明

> 原文档: [rtsp_streaming.md](rtsp_streaming.md)

这份文档说明如何把本地视频文件模拟成 RTSP 摄像头，方便 WebUI 或其他 RTSP 客户端在没有真实 IP 摄像头时测试 RTSP 输入链路。

这套流程使用：

- [MediaMTX](https://github.com/bluenviron/mediamtx)：轻量级本地 RTSP 服务。
- `ffmpeg`：把本地视频文件推送到 MediaMTX，形成 RTSP 流。

## 前置条件

先安装 `ffmpeg`：

```bash
ffmpeg -version
```

从官方 GitHub Releases 页面下载 MediaMTX：

```text
https://github.com/bluenviron/mediamtx/releases
```

选择与你的操作系统和 CPU 架构匹配的压缩包，解压后保留 `mediamtx` 可执行文件和 `mediamtx.yml` 配置文件。下面示例假设目录结构如下：

```text
.
├── rtsp/
│   ├── mediamtx.sh
│   └── rtsp.sh
├── tools/
│   └── mediamtx/
│       ├── mediamtx
│       └── mediamtx.yml
└── videos/
    └── example.mp4
```

这些路径只是示例。你可以把二进制文件、配置文件和视频文件放在任意合适位置。

## 辅助脚本

可以创建一个小的 `rtsp/` 工作目录，放两个辅助脚本。

### `rtsp/mediamtx.sh`

启动本地 MediaMTX RTSP 服务：

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MEDIAMTX_BIN="${MEDIAMTX_BIN:-$REPO_ROOT/tools/mediamtx/mediamtx}"
MEDIAMTX_CONFIG="${MEDIAMTX_CONFIG:-$REPO_ROOT/tools/mediamtx/mediamtx.yml}"

exec "$MEDIAMTX_BIN" "$MEDIAMTX_CONFIG"
```

### `rtsp/rtsp.sh`

循环读取本地视频文件，并推送到 RTSP 地址：

```bash
#!/usr/bin/env bash
set -euo pipefail

VIDEO_PATH="${1:-${VIDEO_PATH:-./videos/example.mp4}}"
RTSP_URL="${2:-${RTSP_URL:-rtsp://127.0.0.1:8554/fire1}}"

exec ffmpeg \
  -re \
  -stream_loop -1 \
  -i "$VIDEO_PATH" \
  -vf "scale='min(1280,iw)':-2" \
  -c:v libx264 \
  -preset veryfast \
  -tune zerolatency \
  -b:v 2500k \
  -c:a aac \
  -f rtsp \
  -rtsp_transport udp \
  "$RTSP_URL"
```

为两个脚本添加可执行权限：

```bash
chmod +x rtsp/mediamtx.sh rtsp/rtsp.sh
```

## 1. 启动 MediaMTX

先开一个终端运行：

```bash
bash ./rtsp/mediamtx.sh
```

使用默认 MediaMTX 配置时，RTSP 服务监听端口为 `8554`。

## 2. 推送本地视频为 RTSP

再开一个终端运行：

```bash
bash ./rtsp/rtsp.sh ./videos/example.mp4 rtsp://127.0.0.1:8554/fire1
```

如果不传参数，示例脚本使用：

```text
输入视频：./videos/example.mp4
RTSP 输出：rtsp://127.0.0.1:8554/fire1
```

## 指定视频和 RTSP 地址

`rtsp.sh` 支持两个可选参数：

```bash
bash ./rtsp/rtsp.sh <视频路径> <RTSP输出地址>
```

示例：

```bash
bash ./rtsp/rtsp.sh \
  ./videos/demo.mp4 \
  rtsp://127.0.0.1:8554/test
```

也可以通过环境变量指定 RTSP 地址：

```bash
RTSP_URL=rtsp://127.0.0.1:8554/test bash ./rtsp/rtsp.sh ./videos/demo.mp4
```

## 推流参数

`ffmpeg` 的主要参数：

```text
-re                 按真实时间读取输入
-stream_loop -1     无限循环播放输入视频
-vf scale=...       宽度最大缩放到 1280，并保持宽高比
-c:v libx264        使用 H.264 编码
-preset veryfast    使用较快编码预设
-tune zerolatency   降低编码延迟
-b:v 2500k          视频码率 2500 kbps
-c:a aac            音频转 AAC
-f rtsp             输出 RTSP
-rtsp_transport udp 使用 UDP 推送 RTSP
```

如果输入视频没有音轨，并且你的 `ffmpeg` 在 `-c:a aac` 上报错，可以移除音频选项，或添加 `-an`。

## 在 WebUI 中使用

启动 MediaMTX 和 `ffmpeg` 推流脚本后，在 WebUI 的 RTSP 输入框填写：

```text
rtsp://127.0.0.1:8554/fire1
```

如果 WebUI 不在同一台机器上，请把 `127.0.0.1` 换成运行 MediaMTX 的机器 IP 或主机名。

## 常见检查

确认视频文件存在：

```bash
ls -lh ./videos/example.mp4
```

确认 MediaMTX 和 `ffmpeg` 都在运行：

```bash
ps -ef | grep -E 'mediamtx|ffmpeg'
```

如果客户端连不上，先确认端口 `8554` 可以访问，没有被防火墙、容器网络或安全组拦住。
