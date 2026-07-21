## 🚑 故障排查

> 原文档: [troubleshooting.md](troubleshooting.md)


### 启动和服务问题

**Q: `webinfer` 返回 502 错误**  
A: 7060 或 8065 端口上的 vLLM 后端尚未就绪。等待模型加载完成（查看日志），
或使用 `curl http://127.0.0.1:7060/v1/models` 验证。

**Q: 模型从不说话（总是返回 `</silence>`）**  
A: 默认情况下，`FORCE_SILENCE_BEFORE_QUERY=true` 会在没有用户问题时抑制输出。
发送一个 prompt，或将该变量设置为 `false`。

**Q: 浏览器无法访问摄像头**  
A: WebRTC 需要 HTTPS。请确保通过 `https://` 访问 WebUI，并接受自签名证书警告。

**Q: 启动 WebUI 后 ASR/TTS 不工作**  
A: 可选服务必须在 WebUI 之前启动。启动 ASR/TTS 后重启 WebUI。

**Q: `ASR failed: Cannot connect to 127.0.0.1:8994`**  
A: ASR 适配器未运行。使用 `./services/asr/scripts/run.sh all` 启动，然后通过
`curl http://127.0.0.1:8994/health` 验证。

**Q: ASR/TTS 一直打印 `upstream ... is not ready`**  
A: `8993`（ASR）或 `8991`（TTS）上的模型服务尚未就绪。运行
`./install/download-models.sh --all`，然后重启 ASR/TTS，并检查
`curl http://127.0.0.1:8993/v1/models` 或 `curl http://127.0.0.1:8991/v1/models`。

**Q: GPU 显存不足**  
A: 检查 GPU 分配。默认情况下，流式模型使用 GPU 0，摘要模型使用 GPU 1，
ASR/TTS 都默认使用 GPU 2。ASR 使用 `gpu_memory_utilization=0.3`；TTS 使用
`services/tts/config/` 下的部署配置，总 TTS 显存预算为 `0.6`。请根据你的硬件
调整 GPU 和部署配置环境变量。

**Q: `file://` 图片被 webinfer 拒绝**  
A: 配置 `ALLOWED_LOCAL_IMAGE_ROOTS`，使其包含你的帧目录。

### background-agent

**Q: `Missing Codex config: .../services/background-agent/codex-home/config.toml`**

你可以复制 `~/.codex/config.toml` 和 `~/.codex/auth.json`，或者直接把整个 `~/.codex` 目录的内容复制到你指定的 `CODEX_HOME` 目录下。

**提示：** 如果你使用 API 方式登录，any-to-photo 功能可能无法使用。你可以通过提示 Codex 修改以下文件来启用该功能（前提是你有支持 any-to-photo 的 API）：

- `$CODEX_HOME/skills/.system/imagegen/SKILL.md`
- `$CODEX_HOME/skills/.system/imagegen/references/cli.md`
- `$CODEX_HOME/skills/.system/imagegen/scripts/image_gen.py`
