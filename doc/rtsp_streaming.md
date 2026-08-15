# RTSP Local Streaming Guide

> 中文文档: [rtsp_streaming.zh-CN.md](./rtsp_streaming.zh-CN.md)

This document explains how to simulate an RTSP camera from a local video file, so the WebUI or any RTSP client can test the RTSP input path without a physical IP camera.

The setup uses:

- [MediaMTX](https://github.com/bluenviron/mediamtx): a lightweight local RTSP server.
- `ffmpeg`: pushes a local video file into MediaMTX as an RTSP stream.

## Prerequisites

Install `ffmpeg` first:

```bash
ffmpeg -version
```

Download MediaMTX from the official GitHub Releases page:

```text
https://github.com/bluenviron/mediamtx/releases
```

Choose the archive that matches your operating system and CPU architecture, extract it, and keep the `mediamtx` binary and `mediamtx.yml` config file together. The examples below assume a layout like this:

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

These paths are examples only. You can put the binary, config, and video files wherever you prefer.

## Helper Scripts

You can create a small `rtsp/` working directory with two helper scripts.

### `rtsp/mediamtx.sh`

Starts the local MediaMTX RTSP server:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MEDIAMTX_BIN="${MEDIAMTX_BIN:-$REPO_ROOT/tools/mediamtx/mediamtx}"
MEDIAMTX_CONFIG="${MEDIAMTX_CONFIG:-$REPO_ROOT/tools/mediamtx/mediamtx.yml}"

exec "$MEDIAMTX_BIN" "$MEDIAMTX_CONFIG"
```

### `rtsp/rtsp.sh`

Loops a local video file and pushes it to an RTSP URL:

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

Make both scripts executable:

```bash
chmod +x rtsp/mediamtx.sh rtsp/rtsp.sh
```

## 1. Start MediaMTX

Open one terminal and run:

```bash
bash ./rtsp/mediamtx.sh
```

With the default MediaMTX config, the RTSP server listens on port `8554`.

## 2. Push a Local Video as RTSP

Open another terminal and run:

```bash
bash ./rtsp/rtsp.sh ./videos/example.mp4 rtsp://127.0.0.1:8554/fire1
```

If you do not pass arguments, the example script uses:

```text
Input video: ./videos/example.mp4
RTSP output: rtsp://127.0.0.1:8554/fire1
```

## Custom Video and RTSP URL

`rtsp.sh` accepts two optional arguments:

```bash
bash ./rtsp/rtsp.sh <video-path> <rtsp-output-url>
```

Example:

```bash
bash ./rtsp/rtsp.sh \
  ./videos/demo.mp4 \
  rtsp://127.0.0.1:8554/test
```

You can also set the RTSP URL through an environment variable:

```bash
RTSP_URL=rtsp://127.0.0.1:8554/test bash ./rtsp/rtsp.sh ./videos/demo.mp4
```

## Streaming Parameters

The main `ffmpeg` parameters are:

```text
-re                 Read the input at real-time speed
-stream_loop -1     Loop the input video forever
-vf scale=...       Scale width down to at most 1280, preserve aspect ratio
-c:v libx264        Encode video with H.264
-preset veryfast    Use a fast encoder preset
-tune zerolatency   Reduce encoder latency
-b:v 2500k          Use a 2500 kbps video bitrate
-c:a aac            Transcode audio to AAC
-f rtsp             Output RTSP
-rtsp_transport udp Push RTSP over UDP
```

If the input video has no audio track and your `ffmpeg` build errors on `-c:a aac`, remove the audio option or add `-an`.

## Low-CPU Mode

The script above scales and re-encodes the video while streaming. This provides good compatibility, but continuously consumes CPU. For a fixed test video, preprocess it once and then stream it in copy mode to significantly reduce CPU usage while streaming.

First, convert the video to H.264 and limit its width to 1280. The WebUI RTSP input only uses the video track, so this command also removes the audio track:

```bash
ffmpeg \
  -i ./videos/example.mp4 \
  -vf "scale='min(1280,iw)':-2" \
  -c:v libx264 \
  -preset medium \
  -crf 23 \
  -pix_fmt yuv420p \
  -an \
  ./videos/example-h264.mp4
```

After preprocessing, loop and publish the video with `-c:v copy`:

```bash
ffmpeg \
  -re \
  -stream_loop -1 \
  -i ./videos/example-h264.mp4 \
  -map 0:v:0 \
  -c:v copy \
  -an \
  -f rtsp \
  -rtsp_transport udp \
  rtsp://127.0.0.1:8554/fire1
```

Copy mode does not scale or encode the video, so it cannot be combined with transcoding options such as `-vf`, `-preset`, `-tune`, or `-b:v`. If the source file is already compatible H.264 at a suitable resolution, you can skip preprocessing and use the copy command directly.

## Use It in WebUI

After MediaMTX and the `ffmpeg` push are running, enter this RTSP URL in the WebUI RTSP input:

```text
rtsp://127.0.0.1:8554/fire1
```

If the WebUI is running on another machine, replace `127.0.0.1` with the IP address or hostname of the machine running MediaMTX.

## Common Checks

Confirm that the video file exists:

```bash
ls -lh ./videos/example.mp4
```

Confirm that MediaMTX and `ffmpeg` are both running:

```bash
ps -ef | grep -E 'mediamtx|ffmpeg'
```

If the client cannot connect, check that port `8554` is reachable and is not blocked by a firewall, container network, or security group.
