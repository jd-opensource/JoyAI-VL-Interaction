# Container 部署

> 原文档: [README.md](./README.md)

同一份 Docker Compose 配置提供三个互斥硬件规格；启动任一规格时会自动关闭另外两个。

| 规格 | 目标 GPU | 主模型 | `MAX_MODEL_LEN` | 主模型显存利用率 | 记忆策略 |
| --- | --- | --- | ---: | ---: | --- |
| `regular` | 1 × 32GB 主模型 + 3 × 32GB / 3 个 API | BF16 preview | 67,174 | 0.95 | 中期 5 块 / 长期 2 块 |
| `24GB` | 1 × 24GB 主模型 + 3 × 24GB / 3 个 API | INT4 AWQ G32 | 81,920 | 0.95 | 中期 5 块 / 长期 5 块 |
| `16GB` | 1 × 16GB 主模型 + 3 × 16GB / 3 个 API | INT4 AWQ G32 | 32,768 | 0.95 | 中期 3 块 / 长期 1 块 |

三个规格均直接使用对应物理 GPU 的容量，按 1 秒 1 帧处理，每次请求最多发送 1 帧，主模型最多输出 256 token。`16GB` 每次摘要最多 3,000 token。实际显存不是 32GB 时，可相应调整 `regular` 的上下文长度和显存利用率。

## 环境要求

- Linux、Docker Engine、Docker Compose 和 NVIDIA Container Runtime。
- 一张主模型 GPU，以及三张符合所选规格的 API GPU。
- 以下宿主机端口未被占用：`7060`、`8065`、`8070`、`8079`、`8099`、`8991`–`8994`。
- 已准备主模型、摘要模型、ASR 模型和 TTS 模型。

共享模型目录结构：

```text
models/
├── JoyAI-VL-Interaction/       # 仅 regular 使用
├── Qwen3-VL-4B-Instruct/
├── Qwen3-ASR-1.7B/
└── Qwen3-TTS-12Hz-1.7B-CustomVoice/
```

`24GB` 和 `16GB` 还需要一个包含 `int4_awq_g32/` 的目录。

## 快速开始

在项目根目录执行：

```bash
# 1. 拉取官方基础镜像并构建服务镜像。
docker pull python:3.12-slim-bookworm
docker pull node:22-bookworm-slim
docker pull vllm/vllm-openai:v0.22.0
./container/scripts/build-images.sh

# 2. 创建并修改一个规格的本地配置。
cp container/16GB/.env.example container/16GB/.env
${EDITOR:-vi} container/16GB/.env

# 3. 启动并检查所有服务端点。
./container/manage.sh 16GB up
./container/manage.sh 16GB test
```

浏览器访问 `https://<host>:8099`。WebUI 使用自动生成的自签名证书。

## 日常操作

```bash
./container/manage.sh 16GB status
./container/manage.sh 16GB logs
./container/manage.sh 16GB restart
./container/manage.sh 16GB down
```

将 `16GB` 替换为 `regular` 或 `24GB` 即可切换规格。

## 配置说明

- `MODEL_ROOT`：共享常规、摘要、ASR 和 TTS 模型所在目录。
- `MAIN_MODEL_ROOT`：包含 `int4_awq_g32` 的目录，仅 `24GB` 和 `16GB` 必填。
- `MAIN_GPU`、`SUMMARY_GPU`、`ASR_GPU`、`TTS_GPU`：GPU 编号，默认依次为 `0`、`1`、`2`、`3`。
- `CODEX_HOME_HOST`：Codex 配置源，默认为 `../services/background-agent/codex-home`。
- `BACKGROUND_WORKSPACE_HOST`：后台 Agent 的可写工作目录。

相对路径以 `container/docker-compose.yml` 为基准。`.env` 仅用于本机，不要提交 Codex 凭据。精确镜像版本见 `images.lock`。

## 附录：调整记忆与单轮时长

你可以修改当前所用规格的 `.env` 文件，根据实际需求平衡记忆效果、推理延迟和硬件占用：

- `CHUNK`：每个中期记忆块包含的帧数。每轮处理一帧时，一个记忆块的近似时长为 `CHUNK × LIVE_VLM_PROCESS_INTERVAL` 秒。
- `COMPRESS_EVERY_N_CHUNKS`：累计多少个中期记忆块后，将其压缩为长期记忆。
- `MID_TERM_MAX_TOKENS` 和 `MID_TERM_TARGET_TOKEN_COUNT`：每条中期记忆摘要的最大长度和目标长度。
- `LONG_TERM_MAX_TOKENS` 和 `LONG_TERM_TARGET_TOKEN_COUNT`：每条长期记忆摘要的最大长度和目标长度。
- `LONG_TERM_MEMORY_WINDOW`：上下文中保留的长期记忆块数量。
- `LIVE_VLM_PROCESS_INTERVAL`：一次推理 turn 的时间间隔，单位为秒。增大该值可降低处理频率和硬件负载，减小该值可提高交互速度，但会增加计算开销。

缩短记忆长度、减少保留块数或降低处理频率，可以减少上下文和计算资源需求；如果硬件资源充足，则可以适当增大这些参数，以获得更长的记忆和更高的交互频率。
