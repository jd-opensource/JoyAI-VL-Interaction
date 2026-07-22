# JoyVL Interaction WebUI

> 原文档: [README.md](README.md)

实时视觉语言模型交互 WebUI。默认使用阿里云百炼视觉模型；可在页面选择 `qwen3.7-plus`、`qwen3.6-plus` 或 `qwen3.6-flash`。设置 `VLM_PROVIDER=local` 才连接原本地 OpenAI 兼容 VLM 服务。

## 环境设置

仓库级安装入口位于 `install/`，仓库级运行时入口是 `services/scripts/run.sh`。本 README 只说明单组件 WebUI 开发安装和启动。

需要 Python 3.12。

```powershell
conda activate joyai
Set-Location "D:/Study/job/2026/PKU/JoyAI/JoyAI-VL-Interaction/services/webui"
python -m pip install -e .
```

百炼模式的 Key 优先从 `OPENAI_API_KEY` 读取；否则读取仓库外 `D:/Study/job/2026/PKU/JoyAI/API.txt` 中首个 `sk-` 开头的完整行。该文件不得移入仓库或提交。

`local` 模式默认后端地址为：

```text
http://127.0.0.1:8070/v1
```

请确保对应的 VLM 后端服务已经先启动。

## 启动

```powershell
conda activate joyai
Set-Location "D:/Study/job/2026/PKU/JoyAI/JoyAI-VL-Interaction/services/webui"
$env:VLM_PROVIDER = "bailian"
$env:PYTHONPATH = "src"
python -m joy_interaction_webui.server --host 0.0.0.0 --port 8099
```

在浏览器中打开：

```text
https://localhost:8099
```

如果浏览器提示自签名证书警告，请继续访问该站点。如果证书文件缺失，请先生成：

```bash
./scripts/generate_cert.sh
```

## 视频来源

- **Webcam**：直接分析浏览器可访问的摄像头。
- **Local Video**：选择本机 MP4、WebM、MOV 或 AVI 文件。文件仅在浏览器中播放，不上传到 WebUI 服务器；浏览器将视频轨道通过现有 WebRTC 链路发送给后端，再按 `Processing Interval` 抽帧分析。
- **RTSP Stream**：网络摄像头/RTSP 服务的输入，仍属于 Beta 功能。

本地视频建议使用当前 Chrome 或 Edge，并保持 `Frames per Batch=1`；首次测试可将 `Processing Interval` 设置为 `2` 秒以降低百炼调用频率。

## 常用端口

```bash
# 百炼：仅启动 WebUI
$env:VLM_PROVIDER = "bailian"
python -m joy_interaction_webui.server --port 8099

# 原本地 webinfer 兼容模式（需要先启动 8070）
$env:VLM_PROVIDER = "local"
python -m joy_interaction_webui.server --port 8090 --model streaming-infer-adapter --api-base http://127.0.0.1:8070/v1
```

## 停止

```bash
./scripts/stop_server.sh
```
