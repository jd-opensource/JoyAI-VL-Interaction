# JoyVL Interaction WebUI

> 原文档: [README.md](./README.md)

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
https://localhost:8099
```

如果浏览器提示自签名证书警告，请继续访问该站点。如果证书文件缺失，请先生成：

```bash
./scripts/generate_cert.sh
```

## 常用端口

```bash
# 默认脚本：WebUI 8099，后端 8070
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
