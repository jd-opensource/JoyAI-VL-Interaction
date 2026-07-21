<p align="center">
  <img src="img/readme-hero.gif" alt="JoyAI-VL-Interaction banner" width="100%">
</p>

<div align="center">

<h1>JoyAI-VL-Interaction</h1>

<p><strong>⚡ An Open Real-time Video-Language Interaction System</strong></p>

<p>An 8B-scale, fully open vision-language interaction model with a complete deployable system — the model, training recipe, time-aligned interaction data, and a real-time streaming stack, all in one repository.</p>

<p>
  <a href="https://arxiv.org/pdf/2606.14777"><b>📄 arXiv</b></a> |
  <a href="https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/"><b>🚀 Blog</b></a> |
  <a href="https://github.com/jd-opensource/JoyAI-VL-Interaction"><b>💻 Code</b></a> |
  <a href="https://huggingface.co/jdopensource/JoyAI-VL-Interaction-Preview"><b>🤗 Model</b></a> |
  <a href="https://huggingface.co/datasets/jdopensource/JoyAI-VL-Interaction"><b>📦 Dataset</b></a>
</p>

<p>
  <a href="#-quick-start"><b>🚀 Quick Start</b></a> |
  <a href="#-capability"><b>🧩 Capability</b></a> |
  <a href="#-evaluation"><b>📊 Evaluation</b></a> |
  <a href="#-citation"><b>📝 Citation</b></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Model-8B-blue?style=flat-square" alt="8B Model">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/vLLM-Inference-orange?style=flat-square" alt="vLLM">
  <img src="https://img.shields.io/badge/CUDA-12.x-76B900?style=flat-square&logo=nvidia&logoColor=white" alt="CUDA 12.x">
  <img src="https://img.shields.io/badge/License-Apache_2.0-green?style=flat-square" alt="Apache 2.0">
  <img src="https://img.shields.io/badge/Latency-<1s-d61f2c?style=flat-square" alt="Sub-second latency">
</p>

</div>

> 中文文档: [README.zh-CN.md](README.zh-CN.md)

## 🔥 News

- **[2026-06-20]** 🎉 Full open-source release — model weights, deployable system, and technical report are now available.
- **[2026-06-20]** 🚀 Day-0 deployment support via [vLLM-Omni](https://github.com/vllm-project/vllm-omni) ([recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/JD/JoyAI-VL-Interaction.md)).
- **[2026-06-20]** 🎉 Release aligned interaction training data.

[https://github.com/user-attachments/assets/2853fc95-ad21-4972-8206-5f3d19798b14](https://github.com/user-attachments/assets/2853fc95-ad21-4972-8206-5f3d19798b14)

## ✨ Introduction

![JoyAI-VL-Interaction overview](img/overview.png)

The most important moments rarely wait for you to ask. A pot boils over while your hands are full. A toddler wanders toward the stove. The best moment of the game is gone before you can react. Today's AI can't help with moments like these — these models are turn-based by design: they sit quietly until you address them, then answer the question you asked.

We think the next step is a model that's **present like a person**: one that watches what's happening now, decides on its own when a moment is worth a word, speaks up when it matters and stays quiet when it doesn't, and hands off to a stronger model when a problem is hard.

**JoyAI-VL-Interaction** is an 8B-scale, vision-first interaction model released together with its training recipe, its data, and a complete deployable system — all fully open. Point a webcam or a livestream at it and it's immediately present in the scene, watching and responding in real time.

### 🌟 Key Features


|     | Feature                          | Description                                                                                    |
| --- | -------------------------------- | ---------------------------------------------------------------------------------------------- |
| ⚡   | **Real-time Presence**           | Watches continuously and responds in under a second when needed.                               |
| 👁️ | **Vision-triggered Proactivity** | Speaks from what it sees, while staying quiet when nothing matters.                            |
| 🤖  | **Agent Delegation**             | Hands hard subtasks to a background model, API, or agent while continuing to watch the stream. |
| 🔓  | **Fully Open Stack**             | Model, data, training recipe, and deployable system — all released for full reproducibility.   |


## 🚀 Quick Start

```bash
git clone https://github.com/jd-opensource/JoyAI-VL-Interaction.git
cd JoyAI-VL-Interaction

# Install dependencies
./install/install.sh --with-all

# Download all model weights
./install/download-models.sh --all

# Start the core services
./services/scripts/run.sh minimal
```

Then open `https://127.0.0.1:8199` in your browser.

The WebUI automatically downloads and starts LiveKit Server 1.13.2. For remote access, only one TCP port (default `8199`, for WebUI HTTPS and proxied LiveKit signaling) and one UDP port (default `8299`, for WebRTC media) need to be exposed. LiveKit's internal signaling port, `TCP 8298`, listens on `127.0.0.1` only and does not need to be exposed. See the [WebUI documentation](services/webui/README.md#livekit-server-and-network-ports) for version, download, and port configuration details.

👉 For the full setup (ASR, TTS, background agent) and configuration details, see the [Getting Started Guide](doc/getting_started.md).

🚑 If you run into deployment issues, see the [Troubleshooting Guide](doc/troubleshooting.md).

## 🛠️ System Architecture

![JoyAI-VL-Interaction system architecture](img/joyvl-system-architecture.png)

At the core of JoyAI-VL-Interaction is one decision the model makes on its own, every second: **speak**, stay **silent**, or **delegate**. We build it on JoyAI-VL-8B and keep speech as pluggable input/output, so the model's only job is to watch and judge the right moment to act. A predictive video codec (AdaCodec) spends only a handful of tokens on each predictable frame and saves full detail for the moments the scene actually changes, keeping the token budget manageable over long streams. The behavior is learned from more than four million time-aligned clips, and refined with reinforcement learning.

Around the model we build a complete, deployable system:


| Component     | Summary                                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------------------------------- |
| 🧠 **Model**  | JoyAI-VL-Interaction: the first open vision-language interaction model.                                           |
| 📊 **Data**   | 4M time-aligned interaction samples, with clear gains from scaling further.                                       |
| ⚙️ **System** | Five pluggable services — inference, WebUI, ASR, TTS, background agent — running on standard vLLM infrastructure. |


📐 For the full architecture diagram and component details, see the [Architecture Guide](doc/architecture.md).

## 🧩 Capability

Once interactivity is trained into the model itself rather than bolted on by an external harness, a whole class of capabilities comes naturally — being present, acting at the right moment, sensing time, and remembering across a long stream.

![JoyAI-VL-Interaction capability grid](img/capability-grid.svg)

Beyond the nine capabilities above, JoyAI-VL-Interaction can call a live game as it's played, guide you through a recipe step by step while you cook, or generate danmaku-style live comments over a stream on its own. Explore more video demos in the [Capability section of the blog](https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/#capabilities).

## 📊 Evaluation

We evaluate JoyAI-VL-Interaction in **58 real, event-driven visual interaction settings**, judged pairwise by human raters for both response quality and timing.

### JoyAI-VL-Interaction vs Doubao


| Aspect                       | JoyAI-VL-Interaction | Tie       | Doubao   |
| ---------------------------- | -------------------- | --------- | -------- |
| Monitoring and alerting      | 100.0%               | 0.0%      | 0.0%     |
| Real-time counting           | 70.0%                | 30.0%     | 0.0%     |
| Real-time translation        | 80.0%                | 20.0%     | 0.0%     |
| Time awareness               | 80.0%                | 10.0%     | 10.0%    |
| Live commentary and guidance | 55.6%                | 22.2%     | 22.2%    |
| Long visual memory           | 77.8%                | 22.2%     | 0.0%     |
| **Overall**                  | **77.6%**            | **17.2%** | **5.2%** |


### JoyAI-VL-Interaction vs Gemini


| Aspect                       | JoyAI-VL-Interaction | Tie       | Gemini   |
| ---------------------------- | -------------------- | --------- | -------- |
| Monitoring and alerting      | 100.0%               | 0.0%      | 0.0%     |
| Real-time counting           | 100.0%               | 0.0%      | 0.0%     |
| Real-time translation        | 100.0%               | 0.0%      | 0.0%     |
| Time awareness               | 50.0%                | 40.0%     | 10.0%    |
| Live commentary and guidance | 100.0%               | 0.0%      | 0.0%     |
| Long visual memory           | 77.8%                | 22.2%     | 0.0%     |
| **Overall**                  | **87.9%**            | **10.3%** | **1.7%** |


## 🚧 Limitations and Future Work

**Limitations.** We want to be upfront about scale. The video-call assistants we compare against, Doubao and Gemini, are backed by far larger models and polished through years of product iteration against real users; they are comprehensive, broadly knowledgeable, and hard to beat on open-ended chat, personal style, and the long tail of everyday requests. JoyAI-VL-Interaction is a compact 8B model, and we don't claim to match them everywhere. What we have done is pry open a door: in the advantage zone of a vision-language interaction model, real-time presence, vision-triggered proactivity, and a sense of time across a stream, a far smaller open model already comes out ahead. That a compact, open model can do this against large, heavily optimized products is exactly why we're excited to put this work in front of the community.

**What is next.**  And we think this is only the beginning. The interaction data we trained on is still small, yet even this much was enough for capabilities we never explicitly taught, like guiding a shopper through changing app screens, to emerge on their own; we're convinced the headroom is large, and that scaling this kind of time-aligned data, together with the recipe and the system, will take the model much further. The moment we are reaching for is an everyday one: you come home worn out after a long day, and before you have said a word, a quiet voice notices and offers, "I can see you're tired; today must have been hard on you." Presence like that, given unasked, is what an interaction model makes possible and a turn-based one, waiting to be addressed, never can. We have released the whole stack openly, the 8B model, the time-aligned data, the training recipe, and the deployable system, to lower the barrier for everyone working in this direction. We'd love for you to explore, with us, what a model that is truly present in the world can become.

## 📂 Repository Layout

```text
.
├── services/
│   ├── scripts/           # Service orchestration entrypoints (run/stop)
│   ├── webinfer/          # Real-time video inference (OpenAI-compatible API)
│   ├── webui/             # Browser frontend + LiveKit streaming
│   ├── asr/               # Speech recognition adapter (Qwen3-ASR)
│   ├── tts/               # Speech synthesis adapter (Qwen3-TTS)
│   └── background-agent/  # Background task delegation agent
├── install/               # Install scripts, dependency setup, model downloads
├── doc/
│   ├── architecture.md          # System architecture & data flow
│   ├── getting_started.md       # Full deployment guide
│   ├── rtsp_streaming.md        # Local RTSP stream testing guide
│   └── *.zh-CN.md               # Chinese documentation mirrors
├── img/                   # Diagrams and figures
├── README.md
├── README.zh-CN.md
├── LICENSE
└── JoyAI-VL-Interaction-Reportv1.pdf
```

## 📋 TODO

- [x] Release interaction model blog
- [x] Release deployable system code
- [x] Release technical report
- [x] Release time-aligned interaction training data
- [x] HuggingFace model 
- [ ] **Unified online + offline model** — a single full-capability model strong at both real-time online (streaming) interaction and offline video understanding
- [ ] **Codec version** — model variant with the predictive video codec (AdaCodec) for lower token cost over long streams
- [ ] **Quantized versions** — for lighter, lower-cost deployment
- [ ] **Optimal inference configs** — tuned setups for RTX 3090 / 5090

## 🙏 Acknowledgments

* Built with [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) and [EasyVideoR1](https://github.com/cyuQ1n/EasyVideoR1) - training frameworks for SFT and RL
* Built on [Live VLM WebUI](https://github.com/nvidia-ai-iot/live-vlm-webui) - NVIDIA's open-source real-time VLM web interface
* Compatible with [vLLM](https://github.com/vllm-project/vllm)

## 📝 Citation

If JoyAI-VL-Interaction helps your research or products, please cite:

```bibtex
@techreport{joyai2026vlinteraction,
  title        = {JoyAI-VL-Interaction: Real-Time Vision-Language Interaction Intelligence},
  author       = {{Video Understanding Team of JoyAI-VL @ Joy Future Academy, JD}},
  institution  = {Joy Future Academy, JD},
  year         = {2026},
  month        = {June}
}
```

## 📄 License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
