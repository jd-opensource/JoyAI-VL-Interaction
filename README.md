# JoyAI-VL-Interaction: An Open Real-time Video-Language Interaction Model

> 🔥 **Full open-source release is coming around June 20, 2026.** The release will include the 8B model, training recipe, time-aligned interaction data, and a complete deployable real-time video-language interaction system.

- 🚀 **Blog**: [JoyAI-VL-Interaction](https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/)
- 💻 **Code**: [jd-opensource/JoyAI-VL-Interaction](https://github.com/jd-opensource/JoyAI-VL-Interaction)
- 📄 **Technical Report**: [JoyAI-VL-Interaction-Reportv1.pdf](https://echovideo.jd.cn/JoyAI-VL-Interaction/JoyAI-VL-Interaction-Reportv1.pdf)

https://github.com/user-attachments/assets/2853fc95-ad21-4972-8206-5f3d19798b14

![JoyAI-VL-Interaction overview](img/overview.png)

## ✨ Introduction

The most important moments rarely wait for you to ask. A pot boils over while your hands are full. A toddler wanders toward the stove. The best moment of the game is gone before you can react. By the time you'd think to ask an AI, the moment has already passed, because the real world doesn't pause. Today's AI can't help with moments like these, and it isn't a matter of speed. These models are turn-based by design: they sit quietly until you address them, then answer the question you asked. Even the video-call features in today's apps are question-and-answer underneath, reacting only when polled or asked. They were built for conversation, not for being present in a world that keeps moving.

We think the next step is a model that's present like a person: one that watches what's happening now, decides on its own when a moment is worth a word, speaks up when it matters and stays quiet when it doesn't, and hands off to a stronger model when a problem is hard. Thinking Machines Lab recently named this an interaction model. We believe it's the right direction, and we wanted to make it something anyone can build on.

So we're releasing JoyAI-VL-Interaction: an 8B-scale, vision-first interaction model, released together with its training recipe, its data, and a complete, deployable system, all fully OPEN. Point a webcam or a livestream at it and it's immediately present in the scene, watching and responding in real time. Because the model is compact and the system runs on standard infrastructure, anyone can stand up their own always-present assistant from a single repository.

Our goal is simple: to take what interaction models make possible and put it in everyone's hands, helping move multimodal AI from turn-based dialogue toward genuine, real-time presence, openly and together. Here's what that looks like.

1. **Real-time presence**: it watches continuously and responds in under a second when needed.
2. **Vision-triggered proactivity**: it speaks from what it sees, while staying quiet when nothing matters.
3. **Agent delegation**: it can hand hard subtasks to a background model, API, or agent while continuing to watch the stream.
4. **Fully open stack**: we release the model, data, training recipe, and deployable system so the work can be reproduced and extended.

## 🧩 Capability

Once interactivity is trained into the model itself, rather than bolted on by an external harness, a whole class of capabilities comes naturally. These are exactly the things a turn-based assistant can't do well, however fast it answers: being present, acting at the right moment, sensing time, and remembering across a long stream. Here are nine of them, each a natural advantage of building interaction into the model. And for every capability below, we include real screen recordings of Doubao's and Gemini's video-call assistants alongside ours, so the difference in interaction style between an interaction model and a turn-based one is plain to see.

![JoyAI-VL-Interaction capability grid](img/capability-grid.svg)

Beyond the capabilities above, there's a lot more you can do with JoyAI-VL-Interaction, and the examples below show a few. Intuitively, it can also call a live game as it's played, guide you through a recipe step by step while you cook, or generate danmaku-style live comments over a stream on its own. These are just a start, and we'd love for the community to explore many more ways to use it.

Explore more video demos in the [Capability section of the blog](https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/#capabilities).

## 🛠️ Our Approach

![JoyAI-VL-Interaction system architecture](img/joyvl-system-architecture.png)

At the core of JoyAI-VL-Interaction is one decision the model makes on its own, every second: **speak**, stay **silent**, or **delegate**. We build it on our visual-language instruct model, JoyAI-VL-8B, and keep speech as pluggable input and output rather than fusing it into the model, so the model's only job is to watch and judge the right moment to act. To stay real-time over long streams, a predictive video codec (AdaCodec) spends only a handful of tokens on each predictable frame and saves full detail for the moments the scene actually changes, so the token budget grows slowly instead of with every frame. The behavior is learned rather than scripted: we train the model on more than four million time-aligned clips labeled second by second for when to speak, stay silent, or delegate, and refine it with reinforcement learning. We release the data and the full recipe so the result can be reproduced and extended.

Around this model we build a complete, deployable system so it works out of the box. The model is the only part that decides when to act; everything else is a pluggable component arranged around it: streaming ASR and TTS for speech, a long-horizon memory that keeps useful detail across hours, a visualization UI, and a bridge that lets the model hand hard subtasks to any background model, API, or agent and fold the answer back while it keeps watching. The whole stack runs on standard vLLM infrastructure to stay real-time over long sessions, and any component can be swapped for a deployment's own without rebuilding the rest.

| Component | Summary |
|---|---|
| Model | **JoyAI-VL-Interaction**: the first open vision-language interaction model. |
| Data | **4M time-aligned interaction samples**: still far from saturated, with clear gains from scaling further. |
| System | **VL-Interaction System**: a deployable system that works out of the box. |

## 📊 Evaluation

We evaluate JoyAI-VL-Interaction in **58 real, event-driven visual interaction settings**. Each item is recorded as a live video interaction with JoyAI-VL-Interaction and the corresponding in-app video-call assistant, then judged pairwise by human raters for both response quality and timing.

### JoyAI-VL-Interaction vs Doubao

| Aspect | JoyAI-VL-Interaction | Tie | Doubao |
|---|---:|---:|---:|
| Monitoring and alerting | 100.0% | 0.0% | 0.0% |
| Real-time counting | 70.0% | 30.0% | 0.0% |
| Real-time translation | 80.0% | 20.0% | 0.0% |
| Time awareness | 80.0% | 10.0% | 10.0% |
| Live commentary and guidance | 55.6% | 22.2% | 22.2% |
| Long visual memory | 77.8% | 22.2% | 0.0% |
| **Overall** | **77.6%** | **17.2%** | **5.2%** |

### JoyAI-VL-Interaction vs Gemini

| Aspect | JoyAI-VL-Interaction | Tie | Gemini |
|---|---:|---:|---:|
| Monitoring and alerting | 100.0% | 0.0% | 0.0% |
| Real-time counting | 100.0% | 0.0% | 0.0% |
| Real-time translation | 100.0% | 0.0% | 0.0% |
| Time awareness | 50.0% | 40.0% | 10.0% |
| Live commentary and guidance | 100.0% | 0.0% | 0.0% |
| Long visual memory | 77.8% | 22.2% | 0.0% |
| **Overall** | **87.9%** | **10.3%** | **1.7%** |

## 🚧 Limitations and Future Work

**Limitations.** We want to be upfront about scale. The video-call assistants we compare against, Doubao and Gemini, are backed by far larger models and polished through years of product iteration against real users; they are comprehensive, broadly knowledgeable, and hard to beat on open-ended chat, personal style, and the long tail of everyday requests. JoyAI-VL-Interaction is a compact 8B model, and we don't claim to match them everywhere. What we have done is pry open a door: in the advantage zone of a vision-language interaction model, real-time presence, vision-triggered proactivity, and a sense of time across a stream, a far smaller open model already comes out ahead. That a compact, open model can do this against large, heavily optimized products is exactly why we're excited to put this work in front of the community.

**What is next.**  And we think this is only the beginning. The interaction data we trained on is still small, yet even this much was enough for capabilities we never explicitly taught, like guiding a shopper through changing app screens, to emerge on their own; we're convinced the headroom is large, and that scaling this kind of time-aligned data, together with the recipe and the system, will take the model much further. The moment we are reaching for is an everyday one: you come home worn out after a long day, and before you have said a word, a quiet voice notices and offers, "I can see you're tired; today must have been hard on you." Presence like that, given unasked, is what an interaction model makes possible and a turn-based one, waiting to be addressed, never can. We have released the whole stack openly, the 8B model, the time-aligned data, the training recipe, and the deployable system, to lower the barrier for everyone working in this direction. We'd love for you to explore, with us, what a model that is truly present in the world can become.

## 📝 Citation

```bibtex
@techreport{joyai2026vlinteraction,
  title        = {JoyAI-VL-Interaction: Real-Time Vision-Language Interaction Intelligence},
  author       = {{Video Understanding Team of JoyAI-VL @ Joy Future Academy, JD}},
  institution  = {Joy Future Academy, JD},
  year         = {2026},
  month        = {June}
}
```
