## 🚑 Troubleshooting

> 中文文档: [troubleshooting.zh-CN.md](troubleshooting.zh-CN.md)

### Startup and Service Issues

**Q: `webinfer` returns 502 errors**  
A: The vLLM backend on port 7060 or 8065 is not ready. Wait for the model to finish
loading (check logs) or verify it with `curl http://127.0.0.1:7060/v1/models`.

**Q: Model never speaks (always returns `</silence>`)**  
A: By default, `FORCE_SILENCE_BEFORE_QUERY=true` suppresses output when there is no
user query. Either send a prompt or set this variable to `false`.

**Q: Browser cannot access the webcam**  
A: WebRTC requires HTTPS. Ensure you access the WebUI through `https://` and accept
the self-signed certificate warning.

**Q: ASR/TTS not working after starting WebUI**  
A: Optional services must be started before WebUI. Restart WebUI after launching ASR/TTS.

**Q: `ASR failed: Cannot connect to 127.0.0.1:8994`**  
A: The ASR adapter is not running. Start it with `./services/asr/scripts/run.sh all`,
then verify it with `curl http://127.0.0.1:8994/health`.

**Q: ASR/TTS keeps printing `upstream ... is not ready`**  
A: The model service is not ready on `8993` (ASR) or `8991` (TTS). Run
`./install/download-models.sh --all`, then restart ASR/TTS and check
`curl http://127.0.0.1:8993/v1/models` or `curl http://127.0.0.1:8991/v1/models`.

**Q: Out of GPU memory**  
A: Check GPU allocation. By default, the streaming model uses GPU 0, the summary
model uses GPU 1, and ASR/TTS both default to GPU 2. ASR uses
`gpu_memory_utilization=0.3`; TTS uses the deploy config under `services/tts/config/`
with a total TTS memory budget of `0.6`. Adjust the GPU and deploy config environment
variables for your hardware.

**Q: `file://` images rejected by webinfer**  
A: Configure `ALLOWED_LOCAL_IMAGE_ROOTS` to include your frame directory.

### background-agent

**Q: `Missing Codex config: .../services/background-agent/codex-home/config.toml`**

You can copy `~/.codex/config.toml` and `~/.codex/auth.json`, or simply copy the entire contents of `~/.codex` to your designated `CODEX_HOME` directory.

**Tip:** If you log in using the API method, the any-to-photo feature may not work. You can enable this feature by prompting Codex to modify the following files (provided that you have an API capable of any-to-photo):

- `$CODEX_HOME/skills/.system/imagegen/SKILL.md`
- `$CODEX_HOME/skills/.system/imagegen/references/cli.md`
- `$CODEX_HOME/skills/.system/imagegen/scripts/image_gen.py`
